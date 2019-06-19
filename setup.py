# -*- coding: utf-8 -*-

# Learn more: https://github.com/kennethreitz/setup.py

from setuptools import setup, find_packages


with open('README.md') as f:
    readme = f.read()

with open('LICENSE') as f:
    license = f.read()

setup(
    name='qrevpy',
    version='0.1.0',
    description='QRev port to python',
    long_description=readme,
    author='David S. Mueller',
    author_email='dmueller@usgs.gov',
    url='https://hydroacoustics.usgs.gov/movingboat/QRev.shtml',
    license=license,
	REQUIRES_PYTHON = '>=3.6.3',
	packages=['QRev', 'QRev.Classes', 'QRev.MiscLibs', 'QRev.UI'],
	 install_requires=[
						"atomicwrites==1.3.0",
						"attrs==19.1.0",
						"Click==7.0",
						"colorama==0.4.1",
						"cycler==0.10.0",
						"kiwisolver==1.1.0",
						"matplotlib==3.0.3",
						"more-itertools==7.0.0",
						"numpy==1.15.2",
						"pandas==0.23.4",
						"patsy==0.5.1",
						"pluggy==0.11.0",
						"py==1.8.0",
						"pyparsing==2.4.0",
						"PyQt5==5.12.2",
						"PyQt5-sip==4.19.17",
						"pyqt5-tools==5.12.1.1.5rc4",
						"pytest==3.8.2",
						"python-dateutil==2.8.0",
						"python-dotenv==0.10.2",
						"pytz==2019.1",
						"scipy==1.1.0",
						"sip==4.19.8",
						"six==1.12.0",
						"statsmodels==0.9.0",
						"utm==0.4.2",
						"xmltodict==0.11.0"
					  ],
)