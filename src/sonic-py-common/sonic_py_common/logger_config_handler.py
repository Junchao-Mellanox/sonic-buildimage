import queue
import redis
import time
import threading

CONFIG_DB = 4
SUBSCRIBE_PATTERN = '__keyspace@4__:LOGGER|*'
SUBSCRIBE_TIMEOUT = 1
LOGGER_TABLE_NAME = 'LOGGER'
LOGGER_FIELD_LEVEL = 'LOGLEVEL'
TABLE_SEPARATOR = '|'
MESSAGE_FIELD_CHANNEL = 'channel'
MESSAGE_FIELD_DATA = 'data'
MESSAGE_OP_HSET = 'hset'


def format_table_key(db_name):
    return '{}{}{}'.format(LOGGER_TABLE_NAME, TABLE_SEPARATOR, db_name)


class LoggerConfigThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        # queue to store logger instances that are pending registered
        self.queue = queue.Queue()
        
        # dict to store <db_key, log_instances>
        self.log_registry = {}
        self.running = False
        
    def stop(self):
        self.running = False
        
    def run(self):
        self.running = True
        db = None
        
        # Wait until DB is ready. Logger instance shall use default 
        # log level before DB is ready.
        while True:
            if not self.running:
                return
            try:
                # Cannot use redis related APIs defined in swsscommon because there is a potential
                # issue in swsscommon.i which calls PyEval_RestoreThread in a C++ destructor.
                # PyEval_RestoreThread may raise exception abi::__forced_unwind while python is
                # exiting. C++ standard does not allow exception in a destructor and it would cause
                # unexpected termination. A detailed explanation can be found in 
                # PR description https://github.com/sonic-net/sonic-buildimage/pull/12255.
                db = redis.Redis(db=4, encoding="utf-8", decode_responses=True)
                pubsub = db.pubsub()
                pubsub.psubscribe(SUBSCRIBE_PATTERN)
                break
            except:
                time.sleep(5)
                continue
            
        while self.running:
            # Process registered logger instance from the queue
            while self.running:
                try:
                    item = self.queue.get_nowait()
                    log_instance = item[0]
                    db_key = item[1]
                    default_level = item[2]
                    table_key = format_table_key(db_key)
                    log_level = db.hget(table_key, LOGGER_FIELD_LEVEL)
                    if db_key not in self.log_registry:
                        # register logger to DB if the db_key is new
                        self.log_registry[db_key] = [log_instance]
                        if not log_level:
                            db.hset(table_key, LOGGER_FIELD_LEVEL, default_level)
                    else:
                        self.log_registry[db_key].append(log_instance)
                    if log_level:
                        log_instance.set_min_log_priority(log_instance.log_priority_from_str(log_level))
                except queue.Empty:
                    # no more registered logger instance, break the loop
                    break
            
            try:
                message = pubsub.get_message()
                if message:
                    key = message[MESSAGE_FIELD_CHANNEL].split(':', 1)[1]
                    db_name = key.split(TABLE_SEPARATOR)[1]
                    op = message[MESSAGE_FIELD_DATA]
                
                    if op != MESSAGE_OP_HSET or db_name not in self.log_registry:
                        continue
                    
                    log_level = db.hget(key, LOGGER_FIELD_LEVEL)
                    # update log level for all log instances with the given db_name
                    for log_instance in self.log_registry[db_name]:
                        log_instance.set_min_log_priority(log_instance.log_priority_from_str(log_level))
                else:
                    time.sleep(SUBSCRIBE_TIMEOUT)
            except redis.exceptions.ConnectionError:
                # redis server is done, exit
                return


class LoggerConfigHandler:
    # global Logger configuration thread instance
    log_config_thread = LoggerConfigThread()
    
    # flag to indicate that log thread has started
    log_thread_started = False
    
    # lock to protect log_thread_started
    log_thread_lock = threading.Lock()
    
    @classmethod
    def start(cls):
        """Start logger configuration thread if it is not started yet
        """
        if not cls.log_thread_started:
            with cls.log_thread_lock:
                if not cls.log_thread_started:
                    cls.log_config_thread.start()
                    cls.log_thread_started = True

    @classmethod
    def stop(cls):
        """Stop logger configuration thread, only used in UT
        """
        with cls.log_thread_lock:
            if cls.log_thread_started:
                cls.log_config_thread.stop()
                cls.log_config_thread.join()
                cls.reset()
                    
    @classmethod
    def reset(cls):
        """Reset logger configuration thread for multi-processing case.

           Linux does not clone the thread while doing fork. In case user creates a sub process
           and still want to set log level on fly, user must call this function before creating
           logger instance.
        """
        cls.log_config_thread = LoggerConfigThread()
        cls.log_thread_started = False
                    
    @classmethod
    def register(cls, instance, db_key, default_level):
        """Register logger instance. A new logger entry will be added to CONFIG DB LOGGER
        table once logger configuration thread is started.

        Args:
            instance (object): logger instance.
            db_key (str): key of LOGGER table. Usually use the log identifier as the key.
            default_level (str): one of NOTICE, DEBUG, INFO, ERROR, WARNING.
        """
        cls.log_config_thread.queue.put((instance, db_key, default_level))
