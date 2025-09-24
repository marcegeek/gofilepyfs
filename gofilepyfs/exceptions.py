class PathError(Exception):
    def __init__(self, msg, path):
        self.msg = msg
        self.path = path
        super().__init__(msg, path)

    def __str__(self):
        return f'{self.msg}: {str(self.path)!r}'


class PathNotFoundError(PathError):
    def __init__(self, path):
        super().__init__('No such path', path)


class PathNotADirectoryError(PathError):
    def __init__(self, path):
        super().__init__('Not a directory', path)


class PathNotAFileError(PathError):
    def __init__(self, path):
        super().__init__('Not a file', path)
