from glob import glob
from setuptools import find_packages, setup

setup(
    name='mini_rover_bridge',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/mini_rover_bridge']),
        ('share/mini_rover_bridge', ['package.xml']),
        ('share/mini_rover_bridge/launch', glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools', 'pyserial'],
    zip_safe=True,
    maintainer='Mini Rover maintainers',
    maintainer_email='maintainers@example.com',
    description='Read-only USB motor telemetry bridge',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={'console_scripts': ['motor_bridge = mini_rover_bridge.node:main']},
)
