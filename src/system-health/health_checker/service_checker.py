import docker
import os
import pickle
import re


import swsssdk
from sonic_py_common import multi_asic
from sonic_py_common.logger import Logger
from .health_checker import HealthChecker
from . import utils

SYSLOG_IDENTIFIER = 'service_checker'
logger = Logger(log_identifier=SYSLOG_IDENTIFIER)


class ServiceChecker(HealthChecker):
    """
    Checker that checks critical system service status via monit service.
    """

    # Cache file to save critical_process_dict
    CRITICAL_PROCESS_CACHE = '/tmp/critical_process_cache'

    CRITICAL_PROCESSES_PATH = 'etc/supervisor/critical_processes'

    # Command to get merged directory of a container
    GET_CONTAINER_FOLDER_CMD = 'docker inspect {} --format "{{{{.GraphDriver.Data.MergedDir}}}}"'

    # Command to query the status of monit service.
    CHECK_MONIT_SERVICE_CMD = 'systemctl is-active monit.service'

    # Command to get summary of critical system service.
    CHECK_CMD = 'monit summary -B'
    MIN_CHECK_CMD_LINES = 3

    # Expect status for different system service category.
    EXPECT_STATUS_DICT = {
        'System': 'Running',
        'Process': 'Running',
        'Filesystem': 'Accessible',
        'Program': 'Status ok'
    }
    
    def __init__(self):
        HealthChecker.__init__(self)
        self.critical_process_dict = {}
        # Containers that has invalid critical_processes file
        self.bad_containers = set()

        self.container_feature_dict = {}

        self.need_save_cache = False

        self.load_critical_process_cache()

    def get_expected_runnning_container_set(self, feature_table):
        """Get a set of containers that are expected to running on SONiC

        Args:
            feature_table (object): FEATURE table in CONFIG_DB

        Returns:
            set: A set of container names
        """
        containers = set()
        container_feature_dict = {}
        for feature_name in feature_table.keys():
            if feature_table[feature_name]["state"] not in ["disabled", "always_disabled"]:
                if multi_asic.is_multi_asic():
                    if feature_table[feature_name]["has_global_scope"] == "True":
                        containers.add(feature_name)
                        container_feature_dict[feature_name] = feature_name
                    if feature_table[feature_name]["has_per_asic_scope"] == "True":
                        num_asics = multi_asic.get_num_asics()
                        for asic_id in range(num_asics):
                            containers.add(feature_name + str(asic_id))
                            container_feature_dict[feature_name + str(asic_id)] = feature_name
                else:
                    containers.add(feature_name)
                    container_feature_dict[feature_name] = feature_name

        return containers, container_feature_dict

    def get_current_running_container_set(self):
        """Get current running containers, if the running container is not in self.critical_process_dict,
           try get the critical process list

        Returns:
            set: A set of running containers
        """
        DOCKER_CLIENT = docker.DockerClient(base_url='unix://var/run/docker.sock')
        running_containers = set()
        ctrs = DOCKER_CLIENT.containers
        try:
            lst = ctrs.list(filters={"status": "running"})

            for ctr in lst:
                running_containers.add(ctr.name)
                if ctr.name not in self.critical_process_dict:
                    self.get_critical_process_by_container(ctr.name)
        except docker.errors.APIError as err:
            logger.log_debug("Failed to retrieve the running container list. Error: '{}'".format(err))
            pass
        return running_containers

    def get_critical_process_list_from_file(self, container, critical_processes_file):
        """
        @summary: Read the critical processes from CRITICAL_PROCESSES_FILE.
        @return: A list which contain critical processes.
        """
        critical_process_list = []

        with open(critical_processes_file, 'r') as file:
            for line in file:
                # ignore blank lines
                if re.match(r"^\s*$", line):
                    continue
                line_info = line.strip(' \n').split(':')
                if len(line_info) != 2:
                    # Invalid syntax in critical_processes file, save it as an error
                    self.bad_containers.add(container)
                    logger.log_error('Invalid syntax in critical_processes file of {}'.format(container))
                    continue

                identifier_key = line_info[0].strip()
                identifier_value = line_info[1].strip()
                if identifier_key == "program" and identifier_value:
                    # We only count lines like "program:<name>"
                    critical_process_list.append(identifier_value)

        return critical_process_list

    def get_critical_process_by_container(self, container):
        """Get critical process for a given container

        Args:
            container (str): container name
        """
        # Get container volumn folder
        container_folder = self._get_container_folder(container)
        if not container_folder:
            logger.log_debug('Failed to get container folder for {}'.format(container_folder))
            return

        # If container folder does not exist, the container is probably not up, retry it
        if not os.path.exists(container_folder):
            logger.log_debug('Container folder does not exist: {}'.format(container_folder))
            return

        # Get critical_processes file path
        critical_processes_file = os.path.join(container_folder, ServiceChecker.CRITICAL_PROCESSES_PATH)
        if not os.path.isfile(critical_processes_file):
            # Critical process file does not exist, the container has no critical processes.
            # This is fine, don't retry.
            logger.log_debug('Failed to get critical process file for {}, {} does not exist'.format(container, critical_processes_file))
            self._update_critical_process_dict(container, [])
            return

        # Get critical process list from critical_processes
        critical_process_list = self.get_critical_process_list_from_file(container, critical_processes_file)
        self._update_critical_process_dict(container, critical_process_list)
        return

    def _update_critical_process_dict(self, container, critical_process_list):
        self.critical_process_dict[container] = critical_process_list
        self.need_save_cache = True

    def _get_container_folder(self, container):
        return utils.run_command(ServiceChecker.GET_CONTAINER_FOLDER_CMD.format(container))

    def save_critical_process_cache(self):
        """Save self.critical_process_dict to a cache file
        """
        if not self.need_save_cache:
            return

        self.need_save_cache = True
        if not self.critical_process_dict:
            # if critical_process_dict is empty, don't save it
            return

        if os.path.exists(ServiceChecker.CRITICAL_PROCESS_CACHE):
            # if cache file exists, remove it
            os.remove(ServiceChecker.CRITICAL_PROCESS_CACHE)

        with open(ServiceChecker.CRITICAL_PROCESS_CACHE, 'wb+') as f:
            pickle.dump(self.critical_process_dict, f)

    def load_critical_process_cache(self):
        if not os.path.isfile(ServiceChecker.CRITICAL_PROCESS_CACHE):
            # cache file does not exist
            return

        with open(ServiceChecker.CRITICAL_PROCESS_CACHE, 'rb') as f:
            self.critical_process_dict = pickle.load(f)

    def reset(self):
        self._info = {}

    def get_category(self):
        return 'Services'

    def check_by_monit(self, config):
        """
        et and analyze the output of $CHECK_CMD, collect status for file system or customize checker if any.
        :param config: Health checker configuration.
        :return:
        """
        output = utils.run_command(ServiceChecker.CHECK_MONIT_SERVICE_CMD)
        if not output or output != 'active':
            self.set_object_not_ok('Service', 'monit', 'monit service is not running')
            return

        output = utils.run_command(ServiceChecker.CHECK_CMD)
        lines = output.splitlines()
        if not lines or len(lines) < ServiceChecker.MIN_CHECK_CMD_LINES:
            self.set_object_not_ok('Service', 'monit', 'output of \"monit summary -B\" is invalid or incompatible')
            return

        status_begin = lines[1].find('Status')
        type_begin = lines[1].find('Type')
        if status_begin < 0 or type_begin < 0:
            self.set_object_not_ok('Service', 'monit', 'output of \"monit summary -B\" is invalid or incompatible')
            return

        for line in lines[2:]:
            name = line[0:status_begin].strip()
            if config and config.ignore_services and name in config.ignore_services:
                continue
            status = line[status_begin:type_begin].strip()
            service_type = line[type_begin:].strip()
            if service_type not in ServiceChecker.EXPECT_STATUS_DICT:
                continue
            expect_status = ServiceChecker.EXPECT_STATUS_DICT[service_type]
            if expect_status != status:
                self.set_object_not_ok(service_type, name, '{} is not {}'.format(name, expect_status))
            else:
                self.set_object_ok(service_type, name)
        return

    def check_services(self, config):
        """Check status of critical services and critical processes

        Args:
            config (config.Config): Health checker configuration.
        """
        config_db = swsssdk.ConfigDBConnector()
        config_db.connect()
        feature_table = config_db.get_table("FEATURE")
        expected_running_containers, self.container_feature_dict = self.get_expected_runnning_container_set(feature_table)
        current_running_containers = self.get_current_running_container_set()
        self.save_critical_process_cache()

        not_running_containers = expected_running_containers.difference(current_running_containers)
        for container in not_running_containers:
            self.set_object_not_ok('Service', container, "Container '{}' is not running".format(container))

        if not self.critical_process_dict:
            # Critical process is empty, not expect
            self.set_object_not_ok('Service', 'system', 'no critical process found')
            return

        for container, critical_process_list in self.critical_process_dict.items():
            self.check_process_existence(container, critical_process_list, config, feature_table)

        for bad_container in self.bad_containers:
            self.set_object_not_ok('Service', bad_container, 'Syntax of critical_processes file is incorrect')

    def check(self, config):
        """
        Check critical system service status. 
        :param config: Health checker configuration.
        :return:
        """
        self.reset()
        self.check_by_monit(config)
        self.check_services(config)
        

    def _parse_supervisorctl_status(self, process_status):
        """Expected input:
            arp_update                       RUNNING   pid 67, uptime 1:03:56
            buffermgrd                       RUNNING   pid 81, uptime 1:03:56

        Args:
            process_status (list): List of process status
        """
        data = {}
        for line in process_status:
            line = line.strip()
            if not line:
                continue
            items = line.split()
            data[items[0].strip()] = items[1].strip()
        return data

    def check_process_existence(self, container_name, critical_process_list, config, feature_table):
        """
        @summary: Check whether the process in the specified container is running or not.
        """
        feature_name = self.container_feature_dict[container_name]
        if feature_name in feature_table:
            # We look into the 'FEATURE' table to verify whether the container is disabled or not.
            # If the container is diabled, we exit.
            if ("state" in feature_table[feature_name]
                    and feature_table[feature_name]["state"] not in ["disabled", "always_disabled"]):

                # We are using supervisorctl status to check the critical process status. We cannot leverage psutil here because
                # it not always possible to get process cmdline in supervisor.conf. E.g, cmdline of orchagent is "/usr/bin/orchagent",
                # however, in supervisor.conf it is "/usr/bin/orchagent.sh"
                cmd = 'docker exec {} bash -c "supervisorctl status"'.format(container_name)
                process_status = utils.run_command(cmd)
                if process_status is None:
                    for process_name in critical_process_list:
                        self.set_object_not_ok('Process', '{}:{}'.format(container_name, process_name), "'{}' is not running".format(process_name))
                    return
                
                process_status = self._parse_supervisorctl_status(process_status.strip().splitlines())
                for process_name in critical_process_list:
                    if config and config.ignore_services and process_name in config.ignore_services:
                        continue

                    # Sometimes process_name is in critical_processes file, but it is not in supervisor.conf, such process will not run in container.
                    # and it is safe to ignore such process. E.g, radv. So here we only check those processes which are in process_status.
                    if process_name in process_status:
                        if process_status[process_name] != 'RUNNING':
                            self.set_object_not_ok('Process', '{}:{}'.format(container_name, process_name), "'{}' is not running".format(process_name))
                        else:
                            self.set_object_ok('Process', '{}:{}'.format(container_name, process_name))
