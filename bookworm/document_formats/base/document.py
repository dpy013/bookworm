# coding: utf-8

import gc
from abc import ABCMeta, abstractmethod
from collections.abc import Sequence
from functools import cached_property, wraps
from lru import LRU
from pycld2 import detect as detect_language, error as CLD2Error
from pathlib import Path
from bookworm import typehints as t
from bookworm.i18n import LocaleInfo
from bookworm.concurrency import QueueProcess, call_threaded
from bookworm.image_io import ImageIO
from bookworm.utils import normalize_line_breaks, remove_excess_blank_lines
from bookworm.logger import logger
from .exceptions import DocumentIOError, PaginationError
from .elements import *
from .features import DocumentCapability, ReadingMode
from . import tools as doctools


log = logger.getChild(__name__)
PAGE_CACHE_CAPACITY = 300


class BaseDocument(Sequence, metaclass=ABCMeta):
    """Defines the core interface of a document."""

    __internal__ = False
    """A flag to indicate whether this type of document is visible to the user."""

    format: str = None
    """The developer oriented format of this document."""

    name: str = None
    """The displayable name of this document format."""

    extensions: t.Tuple[str] = None
    """The file extension(s) of this format."""

    capabilities: DocumentCapability = DocumentCapability.NULL_CAPABILITY
    """A combination of DocumentCapability flags."""

    supported_reading_modes: t.Tuple[ReadingMode] = (ReadingMode.DEFAULT,)

    document_classes: list = []

    def __init_subclass__(cls, *args, **kwargs):
        super().__init_subclass__(*args, **kwargs)
        if cls.format is not None:
            cls.document_classes.append(cls)

    def __init__(self, uri):
        self.uri = uri

    def __contains__(self, value: int):
        return -1 < value < len(self)

    def __getitem__(self, index: int) -> "BasePage":
        if index not in self:
            raise PaginationError(f"Page {index} is out of range.")
        if index in self._page_cache:
            return self._page_cache[index]
        page = self.get_page(index)
        self._page_cache[index] = page
        return page

    def __getstate__(self) -> dict:
        """Support for pickling."""
        return dict(uri=self.uri)

    def __setstate__(self, state):
        """Support for unpickling."""
        self.__dict__.update(state)
        self.read()

    @cached_property
    def reading_options(self):
        reading_mode = int(
            self.uri.openner_args.get("reading_mode", ReadingMode.DEFAULT)
        )
        return ReadingOptions(
            reading_mode=ReadingMode(reading_mode),
        )

    @cached_property
    def identifier(self) -> str:
        """Return a unique identifier for this document."""
        return self.uri.to_uri_string()

    @abstractmethod
    def read(self):
        """
        Perform the actual IO operations for loading the ebook.
        Subclasses should call super to ensure the standard behavior.
        """
        self._page_cache = LRU(PAGE_CACHE_CAPACITY)
        self._page_content_cache = LRU(PAGE_CACHE_CAPACITY)
        # XXX Is this a pre-mature optimization?
        call_threaded(lambda: self.language)

    @abstractmethod
    def close(self):
        """Perform the actual IO operations for unloading the ebook.
        Subclasses should call super to ensure the standard behavior.
        """
        self._page_cache.clear()
        self._page_content_cache.clear()
        gc.collect()

    @abstractmethod
    def get_page(self, index: int) -> "BasePage":
        """Return the page object at index."""

    def get_page_number_from_page_label(self, page_label):
        if DocumentCapability.PAGE_LABELS not in self.capabilities:
            raise NotImplementedError(
                "This feature is not enabled for this class of documents"
            )
        for page in self:
            page_label = page_label.lower()
            if page.get_label().lower() == page_label:
                return page
        raise LookupError(f"Failed to find a page with the label {page_label}.")

    def is_encrypted(self) -> bool:
        """Does this document need password."""
        return False

    def decrypt(self, password):
        """Decrypt this document using the provided password."""
        raise NotImplementedError

    @property
    @abstractmethod
    def toc_tree(self) -> t.Iterable[Section]:
        """Return an iterable representing the table of content.
        The items should be of type `Section`.
        """

    @cached_property
    def language(self) -> str:
        num_pages = len(self)
        num_samples = num_pages if num_pages <= 20 else 20
        text = "".join(self[i].get_text() for i in range(num_samples)).encode("utf8")
        return self.get_language(samples=text, is_html=False)

    @property
    @abstractmethod
    def metadata(self) -> BookMetadata:
        """Return a `BookMetadata` object holding info about this book."""

    def get_page_content(self, page_number: int) -> str:
        """Convenience method: return the text content of a page."""
        _cached = self._page_content_cache.get(page_number)
        if _cached is not None:
            return _cached
        content = self[page_number].get_text()
        self._page_content_cache[page_number] = content
        return content

    def get_page_image(self, page_number: int, zoom_factor: float = 1.0) -> ImageIO:
        """Convenience method: return the image of a page."""
        return self[page_number].get_image(zoom_factor)

    def get_file_system_path(self):
        """Only valid for documents that have true filesystem path."""
        if (filepath := Path(self.uri.path)).exists():
            return filepath
        raise DocumentIOError(f"File {filename} does not exist.")

    @classmethod
    def should_read_async(cls):
        return DocumentCapability.ASYNC_READ in cls.capabilities

    @property
    def is_fluid(self):
        return DocumentCapability.FLUID_PAGINATION in self.capabilities

    @property
    def has_toc_tree(self):
        return DocumentCapability.TOC_TREE in self.capabilities

    @property
    def can_render_pages(self):
        return DocumentCapability.GRAPHICAL_RENDERING in self.capabilities

    @staticmethod
    def get_language(samples, is_html=False, hint_language: str=None) -> LocaleInfo:
        """Return the language of this document.
        By default we use a heuristic based on Google's CLD2.
        """
        try:
            (success, _, ((_, lang, _, _), *_)) = detect_language(
                utf8Bytes=samples, isPlainText=not is_html, hintLanguage=hint_language
            )
        except CLD2Error as e:
            log.error(f"Failed to recognize document language", exc_info=True)
            success = False
        if success:
            return LocaleInfo(lang)
        return LocaleInfo("en")

    def export_to_text(self, target_filename: t.PathLike):
        return QueueProcess(
            target=doctools.export_to_plain_text,
            args=(self, target_filename),
            name="bookworm-exporter",
        )

    def search(self, request: SearchRequest):
        yield from QueueProcess(
            target=doctools.search_book, args=(self, request), name="bookworm-search"
        )


class BasePage(metaclass=ABCMeta):
    """Represents a page from the document."""

    __slots__ = ["document", "index"]

    def __init__(self, document: BaseDocument, index: int):
        self.document = document
        self.index = index

    @abstractmethod
    def get_text(self) -> str:
        """Return the text content or raise NotImplementedError."""

    @abstractmethod
    def get_image(self, zoom_factor: float) -> ImageIO:
        """
        Return page image as `ImageIO`
        or raise NotImplementedError.
        """

    def get_label(self) -> str:
        """Return the page label string (commonly found on PDFs)."""
        return ""

    def normalize_text(self, text):
        return remove_excess_blank_lines(text)

    @property
    def number(self) -> int:
        """The user facing page number."""
        return self.index + 1

    @cached_property
    def section(self) -> Section:
        """The (most specific) section that this page blongs to."""
        rv = self.document.toc_tree
        for sect in rv.iter_children():
            if self.index in sect.pager:
                rv = sect
                if sect.pager.first > self.index:
                    break
        return rv

    @property
    def is_first_of_section(self) -> bool:
        return self.index == self.section.pager.first

    @property
    def is_last_of_section(self) -> bool:
        return self.index == self.section.pager.last

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return (self.document is other.document) and (self.index == other.index)
        return NotImplemented


class FluidPage(BasePage):
    """Emulates a page for a fluid document."""

    def get_text(self):
        return self.document.get_content()

    def get_image(self, zoom_factor=1.0) -> ImageIO:
        raise NotImplementedError


class FluidDocument(BaseDocument):
    """Provides sain defaults for fluid documents."""

    def __len__(self):
        return 1

    def get_page(self, index: int) -> FluidPage:
        return FluidPage(self, index)

    @abstractmethod
    def get_content(self) -> str:
        """Get the content of this document."""
