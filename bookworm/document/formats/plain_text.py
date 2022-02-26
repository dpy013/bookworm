# coding: utf-8

from __future__ import annotations
import os
import ftfy
from functools import cached_property
from bookworm.utils import normalize_line_breaks, remove_excess_blank_lines
from bookworm.logger import logger
from .. import (
    SinglePageDocument,
    Section,
    Pager,
    BookMetadata,
    DocumentCapability as DC,
    DocumentError,
)


log = logger.getChild(__name__)


class PlainTextDocument(SinglePageDocument):
    """For plain text files"""

    format = "txt"
    # Translators: the name of a document file format
    name = _("Plain Text File")
    extensions = ("*.txt",)
    capabilities = DC.SINGLE_PAGE | DC.LINKS | DC.STRUCTURED_NAVIGATION

    def read(self):
        self.filename = self.get_file_system_path()
        with open(self.filename, "r", encoding="utf8") as file:
            self.text = file.read()
        super().read()

    def get_content(self):
        text = remove_excess_blank_lines(self.text)
        return ftfy.ftfy(text)

    def close(self):
        super().close()
        del self.text

    @cached_property
    def toc_tree(self):
        return Section(
            title="",
            pager=Pager(first=0, last=0),
        )

    @cached_property
    def metadata(self):
        return BookMetadata(
            title=os.path.split(self.filename)[-1][:-4],
            author="",
            publication_year="",
        )
