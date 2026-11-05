"""Deployment validation without launching ROS processes or opening hardware."""
import importlib.util
import os
from pathlib import Path

import pytest
from launch import LaunchContext


spec = importlib.util.spec_from_file_location(
    'oc_deployment', Path(__file__).resolve().parents[1] / 'launch' / 'oc.launch.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


def context(**changes):
    result = LaunchContext()
    result.launch_configurations.update({
        'port': '/dev/serial/by-id/verified-v2-board', 'simulated': 'false',
        'pwm_limit': '63', 'log_directory': '~/weeny_ws/run_logs', **changes,
    })
    return result


def test_hardware_default_preserves_provenance_and_limit():
    settings = deployment.deployment_settings(context())
    assert settings['simulated'] is False
    assert settings['pwm_limit'] == 63
    assert settings['baud'] == 115200
    assert settings['log_directory'] == os.path.expanduser('~/weeny_ws/run_logs')


@pytest.mark.parametrize('port', ['/tmp/sim', '/dev/ttyACM0', '/dev/pts/1', ''])
def test_hardware_requires_stable_device_path(port):
    with pytest.raises(ValueError, match='by-id'):
        deployment.deployment_settings(context(port=port))


@pytest.mark.parametrize('pwm_limit', ['0', '64', '-1', '1.5', 'nan'])
def test_rejects_invalid_pwm_limit(pwm_limit):
    with pytest.raises(ValueError, match='pwm_limit'):
        deployment.deployment_settings(context(pwm_limit=pwm_limit))


def test_existing_simulator_pty_and_symlink_are_accepted(tmp_path):
    master, slave = os.openpty()
    try:
        port = os.ttyname(slave)
        link = tmp_path / 'simulator'
        link.symlink_to(port)
        for candidate in (port, str(link)):
            settings = deployment.deployment_settings(context(port=candidate, simulated='true'))
            assert settings['simulated'] is True
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize('port', ['/dev/serial/by-id/board', '/dev/ttyACM0', '/tmp/missing-simulator'])
def test_simulation_cannot_label_usb_or_missing_port_as_simulated(port):
    with pytest.raises(ValueError, match='PTY'):
        deployment.deployment_settings(context(port=port, simulated='true'))


def test_rejects_unrecognized_provenance():
    with pytest.raises(ValueError, match='true or false'):
        deployment.deployment_settings(context(simulated='maybe'))
