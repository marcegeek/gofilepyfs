from __future__ import annotations

import datetime as dt
import functools
import io
import os
import posixpath

from gofilepy import GofileClient, GofileAccount, GofileFolder, GofileContent, GofileFile
from gofilepy.exceptions import GofileAPIContentNotFoundError
from pathlib_abc import PathInfo, vfspath, ReadablePath, PathParser, vfsopen

from .decorators import with_defaults
from .exceptions import PathNotAFileError, PathNotFoundError, PathNotADirectoryError


class GofileFSClient:
    _UPDATE_AFTER = dt.timedelta(seconds=15)

    def __init__(self, token: str) -> None:
        self.gofile: GofileClient = GofileClient(token=token)
        self.account: GofileAccount = self.gofile.account
        self._root_folder: GofileFolder = self.account.root_folder

    @staticmethod
    def _update_root(method):
        def wrapper(self, *args, **kwargs):
            self.ensure_updated(self._root_folder)
            return method(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def _update_folder(method):
        @functools.wraps(method)
        @with_defaults(folder=lambda self: self._root_folder)
        def wrapper(self, *args, **kwargs):
            self.ensure_updated(kwargs.get('folder'))
            return method(self, *args, **kwargs)

        return wrapper

    @property
    @_update_root
    def root_folder(self):
        return self._root_folder

    def get_content(self, folder: GofileFolder, name: str) -> GofileContent | None:
        children = self._get_children(folder=folder)
        filtered = [ch for ch in children if ch.name == name]
        if not filtered:
            return None
        # children are already sorted by creation time in ascending order
        return filtered[-1]

    def get_children(self, folder: GofileFolder) -> list[GofileContent]:
        children = self._get_children(folder=folder)
        names = set([ch.name for ch in children])
        ret = []
        for name in names:
            ret.append([ch for ch in children if ch.name == name][-1])
        ret.sort(key=lambda ch: (ch.is_file_type, ch.name))
        return ret

    @classmethod
    def ensure_updated(cls, content: GofileContent | None = None) -> GofileContent | None:
        if content is None:
            return None
        when_updated = getattr(content, '_when_updated', None)
        if when_updated is None or dt.datetime.now() > when_updated + cls._UPDATE_AFTER:
            try:
                content.reload()
            except GofileAPIContentNotFoundError:
                return None
            content._when_updated = dt.datetime.now()
        return content

    @_update_folder
    def _get_children(self, folder: GofileFolder | None = None) -> list[GofileContent]:
        # sort children by type, name and creation time with folders first
        return sorted(folder.children, key=lambda ch: (ch.is_file_type, ch.name, ch.time_created))


class MissingInfo(PathInfo):
    def exists(self, follow_symlinks: bool = True) -> bool: return False

    def is_dir(self, follow_symlinks: bool = True) -> bool: return False

    def is_file(self, follow_symlinks: bool = True) -> bool: return False

    def is_symlink(self) -> bool: return False


class GofilePathInfo(PathInfo):
    def __init__(self, parser: PathParser, content: GofileContent | None = None, fs_client: GofileFSClient | None = None):
        if content is not None and fs_client is None:
            raise ValueError('fs_client is required when passing a content object')
        self.parser = parser
        self.content = content
        self.fs_client = fs_client

    @staticmethod
    def _update_content(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.fs_client is not None:
                self.content = self.fs_client.ensure_updated(self.content)
            return func(self, *args, **kwargs)

        return wrapper

    @_update_content
    def exists(self, follow_symlinks=True):
        return self.content is not None

    @_update_content
    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return isinstance(self.content, GofileFolder)

    @_update_content
    def is_file(self, follow_symlinks: bool = True) -> bool:
        return isinstance(self.content, GofileFile)

    def is_symlink(self) -> bool:
        return False

    def resolve(self, path: str) -> PathInfo:
        if path in ('', '.'):
            return self
        name, _, path = path.partition(self.parser.sep)
        info = None
        if not name:
            info = self
        elif isinstance(self.content, GofileFolder):
            content = self.fs_client.get_content(self.content, name)
            if content is not None:
                info = GofilePathInfo(self.parser, content=content, fs_client=self.fs_client)
        if info is None:
            return MissingInfo()
        return info.resolve(path)

    @property
    @_update_content
    def children_names(self) -> list[str] | None:
        if isinstance(self.content, GofileFolder):
            return [cont.name for cont in self.fs_client.get_children(self.content)]
        return None


class GofilePath(ReadablePath):
    parser = posixpath

    def __init__(self, *pathsegments: str | os.PathLike, fs_client: GofileFSClient | None = None):
        self._segments = pathsegments
        self.fs_client = fs_client
        self._vfspath = None
        self._root_folder = None

        if self.fs_client is not None:
            self._root_folder = self.fs_client.account.root_folder
            if not hasattr(self._root_folder, 'root_info'):
                # Create the entry point GofilePathInfo object
                self._root_folder.root_info = GofilePathInfo(self.parser, content=self._root_folder, fs_client=self.fs_client)

    def __str__(self):
        return vfspath(self)

    def __repr__(self):
        return f'{self.__class__.__name__}({str(self)!r})'

    @property
    def _joined_segments(self):
        if not self._segments:
            return ''
        return self.parser.join(*self._segments)

    def with_segments(self, *pathsegments):
        return type(self)(*pathsegments, fs_client=self.fs_client)

    def __vfspath__(self):
        if self._vfspath is not None:
            return self._vfspath
        path = self._joined_segments
        if not path:
            self._vfspath = '.'
            return self._vfspath
        _, root, rel = self.parser.splitroot(path)
        parsed = root + self.parser.sep.join(x for x in rel.split(self.parser.sep) if x and x != '.')
        self._vfspath = parsed or '.'
        return self._vfspath

    def resolve(self):
        res = self.parser.normpath(str(self))
        if not self.parser.isabs(res):
            res = self.parser.join(self.parser.sep, res)
        return self.with_segments(res)

    @property
    def info(self) -> GofilePathInfo:
        resolved = self._root_folder.root_info.resolve(str(self.resolve()))
        return resolved

    def __open_reader__(self):
        info = self.info
        if not info.exists():
            raise PathNotFoundError(self)
        if not isinstance(info.content, GofileFile):
            raise PathNotAFileError(self)
        return info.content.download_io()

    def iterdir(self):
        info = self.info
        if not info.exists():
            raise PathNotFoundError(self)
        if not info.is_dir():
            raise PathNotADirectoryError(self)
        return (self / name for name in self.info.children_names)

    def readlink(self):
        raise NotImplementedError

    def open(self, mode='r', buffering=-1, encoding=None, errors=None, newline=None):
        text = 'b' not in mode  # determine if file is opened in text mode
        if text:
            # change mode to binary, removing an explicitly set text mode to be sure
            mode = mode.replace('t', '')
            mode += 'b'
        # open the file without the encoding as it will be opened in binary mode
        file = vfsopen(self, mode=mode, buffering=buffering, errors=errors, newline=newline)
        if text:
            # use the Gofile reported encoding as its default
            if encoding is None:
                encoding = file.encoding
            file = io.TextIOWrapper(file, encoding=encoding, errors=errors, newline=newline)
        return file
