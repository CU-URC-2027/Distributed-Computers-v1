from glob import glob
from setuptools import find_packages, setup
setup(name='mini_rover_c2', version='0.2.0', packages=find_packages(exclude=['test']),
      data_files=[('share/ament_index/resource_index/packages',['resource/mini_rover_c2']),
                  ('share/mini_rover_c2', ['package.xml']),
                  ('share/mini_rover_c2/launch', glob('launch/*.launch.py')),
                  ('share/mini_rover_c2/config', glob('config/*.yaml'))],
      package_data={'mini_rover_c2.legacy': ['*.txt','examples/*.jsonl']},
      install_requires=['setuptools'], zip_safe=False,
      maintainer='Mini Rover maintainers', maintainer_email='maintainers@example.com',
      description='Manual numeric C2 and existing Tk dashboard', license='Apache-2.0', tests_require=['pytest'],
      entry_points={'console_scripts': ['c2_gui = mini_rover_c2.gui:main', 'c2_cli = mini_rover_c2.cli:main']})
