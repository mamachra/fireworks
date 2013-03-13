#!/usr/bin/env python

"""
This module contains some of the most central FireWorks classes:

- A FireTaskBase defines the contract for tasks that run within a FireWork (FireTasks)
- A FWAction encapsulates the output of a FireTask and tells FireWorks what to do next after a job completes
- A FireWork defines a workflow step and contains one or more FireTasks.
- A Launch describes the run of a FireWork on a computing resource.
"""

import datetime
from fireworks.core.fworker import FWorker
from fireworks.utilities.fw_serializers import FWSerializable, recursive_serialize, recursive_deserialize, serialize_fw
from fireworks.utilities.fw_utilities import get_my_host, get_my_ip

__author__ = "Anubhav Jain"
__copyright__ = "Copyright 2013, The Materials Project"
__version__ = "0.1"
__maintainer__ = "Anubhav Jain"
__email__ = "ajain@lbl.gov"
__date__ = "Feb 5, 2013"


class FireTaskBase(FWSerializable):
    """
    FireTaskBase is used as an abstract class that defines a computing task (FireTask). All FireTasks
    should inherit from FireTaskBase.
    """

    def __init__(self, parameters=None):
        """
        :param parameters: Parameters that control the FireTask's operation (custom depending on the FireTask type)
        """
        # When implementing a FireTask, add the following line to the init() to get to_dict to work automatically
        self.parameters = parameters if parameters else {}

    def run_task(self, fw_spec):
        """
        This method gets called when the FireTask is run. It can take in a FireWork spec,
        perform some task using that data, and then return an output in the form of a FWAction.

        :param fw_spec: a FireWork spec (as dict)
        :return: a FWAction instance
        """
        raise NotImplementedError('The FireTask needs to implement run_task()!')

    @serialize_fw
    @recursive_serialize
    def to_dict(self):
        return {"parameters": self.parameters}

    @classmethod
    @recursive_deserialize
    def from_dict(cls, m_dict):
        return cls(m_dict['parameters'])


class FWAction():
    """
    A FWAction encapsulates the output of a FireTask (it is returned by a FireTask after the FireTask completes). The
     FWAction allows a user to store rudimentary output data as well as return commands that alter the workflow.
    """

    commands = ['CONTINUE', 'DEFUSE', 'MODIFY', 'DETOUR', 'CREATE', 'PHOENIX', 'BREAK']

    def __init__(self, command, stored_data=None, mod_spec=None):
        """

        :param command: (String) an item from the list of FWAction.commands
        :param stored_data: (dict) any output data to store. Intended to be brief, not store a ton of data.
        :param mod_spec: description of how to modify the Workflow according to a set of rules (see tutorial docs)
        """
        if command not in FWAction.commands:
            raise ValueError("Invalid command: " + command)

        self.command = command
        self.stored_data = stored_data if stored_data else {}
        self.mod_spec = mod_spec if mod_spec else {}

    @recursive_serialize
    def to_dict(self):
        return {"action": self.command, "stored_data": self.stored_data, "mod_spec": self.mod_spec}

    @classmethod
    @recursive_deserialize
    def from_dict(cls, m_dict):
        if 'create_fw' in m_dict['mod_spec']:
            m_dict['mod_spec']['create_fw'] = FireWork.from_dict(m_dict['mod_spec']['create_fw'])
        return FWAction(m_dict['action'], m_dict['stored_data'], m_dict['mod_spec'])


class FireWork(FWSerializable):
    # 'Canceled' is the dominant spelling over 'cancelled' in the US starting around 1985...(Google n-grams)
    STATE_RANKS = {'DEFUSED': 0, 'WAITING': 1, 'READY': 2, 'FIZZLED': 3, 'RESERVED': 4, 'RUNNING': 5, 'CANCELED': 6,
                   'COMPLETED': 7}

    # TODO: move fw_id as last parameter for consistency (id is always last parameter in constructors a la Launch)
    def __init__(self, tasks, spec=None, fw_id=-1, launches=None, state='WAITING', created_at=None):
        """
        :param tasks: (list) a list of FireTasks to run in sequence
        :param spec: (dict) specification of the job to run. Used by the FireTask
        :param fw_id: (int) the FW's database id (negative numbers will be re-assigned dynamically when they are
        entered in the database through the LaunchPad.
        :param launches: (list) a list of Launch objects of this FireWork
        :param state: (String) the state of the FW (e.g. WAITING, RUNNING, COMPLETED, CANCELED)
        """

        # automatically transform tasks into a list, if not in that format
        if not isinstance(tasks, list):
            tasks = [tasks]

        self.tasks = tasks
        self.spec = spec if spec else {}
        self.spec['_tasks'] = [t.to_dict() for t in tasks]  # put tasks in a special location of the spec
        self.fw_id = fw_id
        self.launches = launches if launches else []
        self.created_at = created_at if created_at else datetime.datetime.utcnow()
        self.state = state

    @recursive_serialize
    def to_dict(self):
        m_dict = {'spec': self.spec, 'fw_id': self.fw_id, 'created_at': self.created_at}

        if len(self.launches) > 0:
            m_dict['launches'] = self.launches

        if self.state != 'WAITING':
            m_dict['state'] = self.state

        return m_dict

    def to_db_dict(self):
        m_dict = self.to_dict()
        m_dict['launches'] = [l.launch_id for l in self.launches]  # the launches are stored separately
        m_dict['state'] = self.state
        return m_dict

    @classmethod
    @recursive_deserialize
    def from_dict(cls, m_dict):
        tasks = m_dict['spec']['_tasks']
        l = m_dict.get('launches', None)
        if l:
            l = [Launch.from_dict(tmp) for tmp in l]
        fw_id = m_dict.get('fw_id', -1)
        state = m_dict.get('state', 'WAITING')
        created_at = m_dict.get('created_at', None)

        return FireWork(tasks, m_dict['spec'], fw_id, l, state, created_at)


class Launch(FWSerializable, object):
    # TODO: update docs
    def __init__(self, state, launch_dir, fworker=None, host=None, ip=None, action=None, state_history=None,
                 launch_id=None, fw_id=None):
        """

        :param fworker: A FWorker object describing the worker
        :param fw_id: id of the FireWork this launch is running
        :param host: the hostname where the launch took place (probably automatically set)
        :param ip: the ip address where the launch took place (probably automatically set)
        :param launch_dir: the directory on the host where the launch took place (probably automatically set)
        :param action: The resulting Action from the launch (set after the launch finished)
        :param state: the state of the Launch
        :param launch_id: the id of the Launch for the LaunchPad
        """
        if state not in FireWork.STATE_RANKS:
            raise ValueError("Invalid launch state: {}".format(state))

        self.fworker = fworker
        self.fw_id = fw_id
        self.host = host if host else get_my_host()
        self.ip = ip if ip else get_my_ip()
        self.launch_dir = launch_dir
        self.action = action if action else None
        self.state_history = state_history if state_history else []
        self.state = state
        self.launch_id = launch_id

    def touch_history(self):
        self.state_history[-1]['updated_at'] = datetime.datetime.utcnow()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = value
        self._update_state_history(value)

    @property
    def time_start(self):
        return self._get_time('RUNNING')

    @property
    def time_end(self):
        return self._get_time(['COMPLETED', 'FIZZLED'])

    @property
    def time_reserved(self):
        return self._get_time('RESERVED')

    @property
    def time_ready(self):
        return self._get_time('READY')

    @property
    def last_pinged(self):
        return self._get_time('RUNNING', True)

    @property
    def runtime_secs(self):
        start = self.time_start
        end = self.time_end
        if start and end:
            return (end - start).total_seconds()

    @property
    def queuedtime_secs(self):
        start = self.time_reserved
        if start:
            end = self.time_start if self.time_start else datetime.datetime.utcnow()
            return (end - start).total_seconds()

    @recursive_serialize
    def to_dict(self):
        return {'fworker': self.fworker, 'fw_id': self.fw_id, 'launch_dir': self.launch_dir, 'host': self.host,
                'ip': self.ip, 'action': self.action, 'state': self.state, 'state_history': self.state_history,
                'launch_id': self.launch_id}

    def to_db_dict(self):
        m_d = self.to_dict()
        m_d['runtime_secs'] = self.runtime_secs
        return m_d

    @classmethod
    @recursive_deserialize
    def from_dict(cls, m_dict):
        fworker = FWorker.from_dict(m_dict['fworker'])
        action = FWAction.from_dict(m_dict['action']) if m_dict.get('action') else None
        return Launch(m_dict['state'], m_dict['launch_dir'], fworker, m_dict['host'], m_dict['ip'], action, m_dict['state_history'], m_dict['launch_id'], m_dict['fw_id'])

    def _update_state_history(self, state):
        last_state = self.state_history[-1]['state'] if len(self.state_history) > 0 else None
        if state != last_state:
            now_time = datetime.datetime.utcnow()
            self.state_history.append({'state': state, 'created_at': now_time, 'updated_at': now_time})

    def _get_time(self, states, use_update_time=False):
        states = states if isinstance(states, list) else [states]
        for data in self.state_history:
            if data['state'] in states:
                if use_update_time:
                    return data['updated_at']
                return data['created_at']