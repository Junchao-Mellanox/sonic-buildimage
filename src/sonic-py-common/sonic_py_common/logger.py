import os
import sys
import syslog
from . import logger_config_handler

"""
Logging functionality for SONiC Python applications
"""


class Logger(object):
    """
    Logger class for SONiC Python applications
    """
    LOG_FACILITY_DAEMON = syslog.LOG_DAEMON
    LOG_FACILITY_USER = syslog.LOG_USER

    LOG_OPTION_NDELAY = syslog.LOG_NDELAY
    LOG_OPTION_PID = syslog.LOG_PID

    LOG_PRIORITY_ERROR = syslog.LOG_ERR
    LOG_PRIORITY_WARNING = syslog.LOG_WARNING
    LOG_PRIORITY_NOTICE = syslog.LOG_NOTICE
    LOG_PRIORITY_INFO = syslog.LOG_INFO
    LOG_PRIORITY_DEBUG = syslog.LOG_DEBUG

    DEFAULT_LOG_FACILITY = LOG_FACILITY_USER
    DEFAULT_LOG_OPTION = LOG_OPTION_NDELAY
    
    config_handler = logger_config_handler.LoggerConfigHandler()

    def __init__(self, log_identifier=None,
                       log_facility=DEFAULT_LOG_FACILITY,
                       log_option=DEFAULT_LOG_OPTION,
                       log_level=LOG_PRIORITY_NOTICE,
                       enable_config_thread=False):
        self._syslog = syslog

        if log_identifier is None:
            log_identifier = os.path.basename(sys.argv[0])

        # Initialize syslog
        self._syslog.openlog(ident=log_identifier, logoption=log_option, facility=log_facility)

        # Set the default log priority
        self.set_min_log_priority(log_level)
        
        Logger.config_handler.register(self, log_identifier, self.log_priority_to_str(log_level))
        
        if enable_config_thread:
            Logger.config_handler.start()

    def __del__(self):
        self._syslog.closelog()

    def log_priority_to_str(self, priority):
        """Convert log priority to string.

        Args:
            priority (int): log priority.

        Returns:
            str: log priority in string.
        """
        if priority == Logger.LOG_PRIORITY_NOTICE:
            return 'NOTICE'
        elif priority == Logger.LOG_PRIORITY_INFO:
            return 'INFO'
        elif priority == Logger.LOG_PRIORITY_DEBUG:
            return 'DEBUG'
        elif priority == Logger.LOG_PRIORITY_WARNING:
            return 'WARN'
        elif priority == Logger.LOG_PRIORITY_ERROR:
            return 'ERROR'
        else:
            self.log_error(f'Invalid log priority: {priority}')
            return 'NOTICE'
    
    def log_priority_from_str(self, priority_in_str):
        """Convert log priority from string.

        Args:
            priority_in_str (str): log priority in string.

        Returns:
            _type_: log priority.
        """
        if priority_in_str == 'DEBUG':
            return Logger.LOG_PRIORITY_DEBUG
        elif priority_in_str == 'INFO':
            return Logger.LOG_PRIORITY_INFO
        elif priority_in_str == 'NOTICE':
            return Logger.LOG_PRIORITY_NOTICE
        elif priority_in_str == 'WARN':
            return Logger.LOG_PRIORITY_WARNING
        elif priority_in_str == 'ERROR':
            return Logger.LOG_PRIORITY_ERROR
        else:
            self.log_error(f'Invalid log priority string: {priority_in_str}')
            return Logger.LOG_PRIORITY_NOTICE
        
    #
    # Methods for setting minimum log priority
    #

    def set_min_log_priority(self, priority):
        """
        Sets the minimum log priority level to <priority>. All log messages
        with a priority lower than <priority> will not be logged

        Args:
            priority: The minimum priority at which to log messages
        """
        self._min_log_priority = priority

    def set_min_log_priority_error(self):
        """
        Convenience function to set minimum log priority to LOG_PRIORITY_ERROR
        """
        self.set_min_log_priority(self.LOG_PRIORITY_ERROR)

    def set_min_log_priority_warning(self):
        """
        Convenience function to set minimum log priority to LOG_PRIORITY_WARNING
        """
        self.set_min_log_priority(self.LOG_PRIORITY_WARNING)

    def set_min_log_priority_notice(self):
        """
        Convenience function to set minimum log priority to LOG_PRIORITY_NOTICE
        """
        self.set_min_log_priority(self.LOG_PRIORITY_NOTICE)

    def set_min_log_priority_info(self):
        """
        Convenience function to set minimum log priority to LOG_PRIORITY_INFO
        """
        self.set_min_log_priority(self.LOG_PRIORITY_INFO)

    def set_min_log_priority_debug(self):
        """
        Convenience function to set minimum log priority to LOG_PRIORITY_DEBUG
        """
        self.set_min_log_priority(self.LOG_PRIORITY_DEBUG)

    #
    # Methods for logging messages
    #

    def log(self, priority, msg, also_print_to_console=False):
        if self._min_log_priority >= priority:
            # Send message to syslog
            self._syslog.syslog(priority, msg)

            # Send message to console
            if also_print_to_console:
                print(msg)

    def log_error(self, msg, also_print_to_console=False):
        self.log(self.LOG_PRIORITY_ERROR, msg, also_print_to_console)

    def log_warning(self, msg, also_print_to_console=False):
        self.log(self.LOG_PRIORITY_WARNING, msg, also_print_to_console)

    def log_notice(self, msg, also_print_to_console=False):
        self.log(self.LOG_PRIORITY_NOTICE, msg, also_print_to_console)

    def log_info(self, msg, also_print_to_console=False):
        self.log(self.LOG_PRIORITY_INFO, msg, also_print_to_console)

    def log_debug(self, msg, also_print_to_console=False):
        self.log(self.LOG_PRIORITY_DEBUG, msg, also_print_to_console)
