# coding: utf-8

from .base import (BaseDocument, BasePage, DummyDocument, SinglePage,
                   SinglePageDocument)
from .elements import (SINGLE_PAGE_DOCUMENT_PAGER, BookMetadata, DocumentInfo,
                       LinkTarget, Pager, Section, TreeStackBuilder)
from .exceptions import (ChangeDocument, DocumentEncryptedError, DocumentError,
                         DocumentIOError, PaginationError)
from .features import READING_MODE_LABELS, DocumentCapability, ReadingMode
from .formats import *


def create_document(uri, read=True):
    doc_cls = BaseDocument.get_document_class_given_format(uri.format.lower())
    if doc_cls is None:
        raise UnsupportedDocumentFormatError(
            f"Document Format {uri.format} is not supported."
        )
    document = doc_cls(uri)
    if read:
        try:
            document.read()
        except ChangeDocument as e:
            return create_document(e.new_uri, read)
    return document
