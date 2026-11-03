from glob import glob
from setuptools import find_packages, setup
setup(name='mini_rover_sim', version='0.2.0', packages=find_packages(exclude=['test']),
      data_files=[('share/ament_index/resource_index/packages',['resource/mini_rover_sim']),
                  ('share/mini_rover_sim', ['package.xml']),
                  ('share/mini_rover_sim/launch', glob('launch/*.launch.py')),
                  ('share/mini_rover_sim/config', glob('config/*.yaml'))],
      package_data={'mini_rover_c2.legacy': ['*.txt','examples/*.jsonl']},
      install_requires=['setuptools'], zip_safe=False,
      maintainer='Mini Rover maintainers', maintainer_email='maintainers@example.com',
      description='Independent Arduino virtual serial simulator', license='Apache-2.0', tests_require=['pytest'],
      entry_points={'console_scripts': ['arduino_sim = mini_rover_sim.simulator:main']})
