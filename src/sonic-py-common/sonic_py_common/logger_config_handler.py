import queue
import time
import threading


class LoggerConfigThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        # queue to store logger instances that are pending registered
        self.queue = queue.Queue()
        
        # dict to store <db_key, log_instances>
        self.log_registry = {}
        
    def run(self):
        from swsscommon import swsscommon
        db = None
        
        # Wait until DB is ready. Logger instance shall use default 
        # log level before DB is ready.
        while True:
            try:
                db = swsscommon.DBConnector("CONFIG_DB", 0, False)
                break
            except:
                time.sleep(5)
                continue
        
        sel = swsscommon.Select()
        logger_table = swsscommon.Table(db, swsscommon.CFG_LOGGER_TABLE_NAME)
        logger_sub_table = swsscommon.SubscriberStateTable(db, swsscommon.CFG_LOGGER_TABLE_NAME)
        sel.addSelectable(logger_sub_table)
            
        while True:
            # Process registered logger instance from the queue
            while True:
                try:
                    item = self.queue.get_nowait()
                    log_instance = item[0]
                    db_key = item[1]
                    default_level = item[2]
                    configured, level = logger_table.hget(db_key, "LOGLEVEL")
                    if db_key not in self.log_registry:
                        # register logger to DB if the db_key is new
                        self.log_registry[db_key] = [log_instance]
                        if not configured:
                            fvs = swsscommon.FieldValuePairs([("LOGLEVEL", default_level)])
                            logger_table.set(db_key, fvs)
                    else:
                        self.log_registry[db_key].append(log_instance)
                    if configured:
                        log_instance.set_min_log_priority(log_instance.log_priority_from_str(level))
                except queue.Empty:
                    # no more registered logger instance, break the loop
                    break
            
            (ret, _) = sel.select(1000)
            if ret == swsscommon.Select.ERROR:
                continue
            
            if ret == swsscommon.Select.TIMEOUT:
                continue
            
            key, op, fvp = logger_sub_table.pop()
            
            if op != swsscommon.SET_COMMAND or key not in self.log_registry:
                continue
            
            fvp = dict(fvp) if fvp is not None else {}
            if 'LOGLEVEL' in fvp:
                # update log level for all log instances with the given key
                for log_instance in self.log_registry[key]:
                    log_instance.set_min_log_priority(log_instance.log_priority_from_str(fvp['LOGLEVEL']))


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
