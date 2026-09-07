"""Reusable commit-boundary/retry probes for isolated test processes.

factory() must create disposable state and yield (operation, snapshot).
operation retries the SAME business request; snapshot returns observable JSON.
This does not make arbitrary submitted code transactional, and does not simulate
power failure, fsync durability, concurrent writers or non-rename commit APIs.
"""
from __future__ import annotations

import contextlib
import copy
import os
from unittest.mock import patch

from MiniClaw.coding_agent.assistant.acceptance import equal, strict_json


@contextlib.contextmanager
def commit_probe(index=None, after=False):
    state={'count':0,'fired':False}
    def wrap(fn):
        def execute(*args,**kwargs):
            state['count']+=1
            fire=state['count']==index
            if fire:
                state['fired']=True
                if not after:
                    raise OSError('Injected failure before commit')
            result=fn(*args,**kwargs)
            if fire:
                raise OSError('Injected lost response after commit')
            return result
        return execute
    with patch('os.replace',wrap(os.replace)),patch('os.rename',wrap(os.rename)):
        yield state


def probe_recovery(factory, max_commits=8):
    """Measure commit points, then fail before/after EACH in a fresh workspace."""
    with factory() as (operation,snapshot):
        initial=copy.deepcopy(snapshot());strict_json(initial)
        with commit_probe() as observed:
            operation()
        expected=copy.deepcopy(snapshot());strict_json(expected)
    count=observed['count']
    if not 1<=count<=max_commits:
        return {'covered':False,'passed':False,'reason':'Commit strategy not covered or exceeds probe bound',
                'observed_commits':count,'probes':[]}
    probes=[]
    for index in range(1,count+1):
        for after in (False,True):
            with factory() as (operation,snapshot):
                require_initial=equal(snapshot(),initial)
                if not require_initial:
                    raise ValueError('factory must reset the same initial test state')
                error=None
                with commit_probe(index,after) as fault:
                    try:
                        operation()
                    except Exception as exc:
                        error=f'{type(exc).__name__}: {exc}'
                try:
                    interrupted=copy.deepcopy(snapshot());strict_json(interrupted)
                    consistent=equal(interrupted,initial) or equal(interrupted,expected)
                except Exception as exc:
                    interrupted=None;consistent=False;error=f'{type(exc).__name__}: {exc}'
                retry_error=None
                try:
                    operation()
                    actual=copy.deepcopy(snapshot());strict_json(actual)
                    retry_ok=equal(actual,expected)
                except Exception as exc:
                    actual=None;retry_ok=False;retry_error=f'{type(exc).__name__}: {exc}'
                probes.append({'commit':index,'after':after,'injected':fault['fired'],
                               'consistent_after_failure':consistent,'retry_exactly_once':retry_ok,
                               'interrupted_state':interrupted,'after_retry':actual,'expected_once':expected,
                               'error':error,'retry_error':retry_error,
                               'passed':fault['fired'] and consistent and retry_ok})
    # The action committed fully but its caller never received the response.
    with factory() as (operation,snapshot):
        if not equal(snapshot(),initial):
            raise ValueError('factory must reset the same initial test state')
        response_loss_error=None
        try:
            operation()
            operation()
            actual=copy.deepcopy(snapshot());strict_json(actual)
            response_loss_ok=equal(actual,expected)
        except Exception as exc:
            response_loss_ok=False
            response_loss_error=f'{type(exc).__name__}: {exc}'
    return {'covered':all(p['injected'] for p in probes),'passed':all(p['passed'] for p in probes) and response_loss_ok,
            'observed_commits':count,'response_lost_retry_exactly_once':response_loss_ok,
            'response_lost_error':response_loss_error,'probes':probes}
