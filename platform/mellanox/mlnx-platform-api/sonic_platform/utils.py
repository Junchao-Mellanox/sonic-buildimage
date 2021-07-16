import functools
import subprocess
from sonic_py_common.logger import Logger

logger = Logger()


def read_from_file(file_path, convert_to, default='', raise_exception=False, log_func=logger.log_error):
    """
    Read content from file and convert to target type
    :param file_path: File path
    :param convert_to: target type
    :param default: Default return value if any exception occur
    :param raise_exception: Raise exception to caller if True else just return default value
    :param log_func: function to log the error
    :return: String content of the file
    """
    try:
        with open(file_path, 'r') as f:
            value = convert_to(f.read().strip())
    except (ValueError, IOError) as e:
        if log_func:
            log_func('Failed to read from file {} - {}'.format(file_path, repr(e)))
        if not raise_exception:
            value = default
        else:
            raise e

    return value


def read_str_from_file(file_path, default='', raise_exception=False, log_func=logger.log_error):
    """
    Read string content from file
    :param file_path: File path
    :param default: Default return value if any exception occur
    :param raise_exception: Raise exception to caller if True else just return default value
    :param log_func: function to log the error
    :return: String content of the file
    """
    return read_from_file(file_path=file_path, convert_to=str, default=default, raise_exception=raise_exception, log_func=log_func)


def read_int_from_file(file_path, default=0, raise_exception=False, log_func=logger.log_error):
    """
    Read content from file and cast it to integer
    :param file_path: File path
    :param default: Default return value if any exception occur
    :param raise_exception: Raise exception to caller if True else just return default value
    :param log_func: function to log the error
    :return: Integer value of the file content
    """
    return read_from_file(file_path=file_path, convert_to=int, default=default, raise_exception=raise_exception, log_func=log_func)


def read_float_from_file(file_path, default=0.0, raise_exception=False, log_func=logger.log_error):
    """
    Read content from file and cast it to integer
    :param file_path: File path
    :param default: Default return value if any exception occur
    :param raise_exception: Raise exception to caller if True else just return default value
    :param log_func: function to log the error
    :return: Integer value of the file content
    """
    return read_from_file(file_path=file_path, convert_to=float, default=default, raise_exception=raise_exception, log_func=log_func)


def _key_value_converter(content):
    ret = {}
    for line in content.splitlines():
        k,v = line.split(':')
        ret[k.strip()] = v.strip()
    return ret


def read_key_value_file(file_path, default={}, raise_exception=False, log_func=logger.log_error):
    """Read file content and parse the content to a dict. The file content should like:
       key1:value1
       key2:value2

    Args:
        file_path (str): file path
        default (dict, optional): default return value. Defaults to {}.
        raise_exception (bool, optional): If exception should be raised or hiden. Defaults to False.
        log_func (optional): logger function.. Defaults to logger.log_error.
    """
    return read_from_file(file_path=file_path, convert_to=_key_value_converter, default=default, raise_exception=raise_exception, log_func=log_func)


def write_file(file_path, content, raise_exception=False, log_func=logger.log_error):
    """
    Write the given value to a file
    :param file_path: File path
    :param content: Value to write to the file
    :param raise_exception: Raise exception to caller if True
    :return: True if write success else False
    """
    try:
        with open(file_path, 'w') as f:
            f.write(str(content))
    except (ValueError, IOError) as e:
        if log_func:
            log_func('Failed to write {} to file {} - {}'.format(content, file_path, repr(e)))
        if not raise_exception:
            return False
        else:
            raise e
    return True


def pre_initialize(init_func):
    def decorator(method):
        @functools.wraps(method)
        def _impl(self, *args, **kwargs):
            init_func(self)
            return method(self, *args, **kwargs)
        return _impl
    return decorator


def pre_initialize_one(init_func):
    def decorator(method):
        @functools.wraps(method)
        def _impl(self, index):
            init_func(self, index)
            return method(self, index)
        return _impl
    return decorator


def read_only_cache():
    """Decorator to cache return value for a method/function once.
       This decorator should be used for method/function when:
       1. Executing the method/function takes time. e.g. reading sysfs.
       2. The return value of this method/function never changes.
    """
    def decorator(method):
        method.return_value = None

        @functools.wraps(method)
        def _impl(*args, **kwargs):
            if not method.return_value:
                method.return_value = method(*args, **kwargs)
            return method.return_value
        return _impl
    return decorator


@read_only_cache()
def is_host():
    """
    Test whether current process is running on the host or an docker
    return True for host and False for docker
    """ 
    try:
        proc = subprocess.Popen("docker --version 2>/dev/null", 
                                stdout=subprocess.PIPE, 
                                shell=True, 
                                stderr=subprocess.STDOUT, 
                                universal_newlines=True)
        stdout = proc.communicate()[0]
        proc.wait()
        result = stdout.rstrip('\n')
        return result != ''
    except OSError as e:
        return False


def default_return(return_value, log_func=logger.log_debug):
    def wrapper(method):
        @functools.wraps(method)
        def _impl(*args, **kwargs):
            try:
                return method(*args, **kwargs)
            except Exception as e:
                if log_func:
                    log_func('Faield to execute method {} - {}'.format(method.__name__, repr(e)))
                return return_value
        return _impl
    return wrapper
