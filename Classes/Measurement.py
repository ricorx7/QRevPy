from Classes.MMT_TRDI import MMT_TRDI
import numpy as np
import os
import scipy.io as sio
from Classes.TransectData import TransectData, allocate_transects
from Classes.PreMeasurement import PreMeasurement
from Classes.MovingBedTests import MovingBedTests
from Classes.QComp import QComp
from Classes.MatSonTek import MatSonTek
from Classes.ComputeExtrap import ComputeExtrap
from Classes.ExtrapQSensitivity import ExtrapQSensitivity
from Classes.Uncertainty import Uncertainty
from Classes.QAData import QAData
from MiscLibs.common_functions import cart2pol, pol2cart, rad2azdeg, nans, azdeg2rad
from Classes.BoatStructure import BoatStructure
import xml.etree.ElementTree as ET
from xml.dom.minidom import parse, parseString
import datetime

class Measurement(object):
    """Class to hold all measurement details.

    Attributes
    ----------
    station_name: str
        Station name
    station_number: str
        Station number
    transects: list
        List of transect objects of TransectData
    mb_tests: list
        List of moving-bed test objects of MovingBedTests
    system_tst: list
        List of system test objects of PreMeasurement
    compass_cal: list
        List of compass calibration objects of PreMeasurement
    compass_eval: list
        List of compass evaluation objects of PreMeasurement
    extrap_fit: ComputeExtrap
        Object of ComputeExtrap
    processing: str
        Type of processing, default QRev
    discharge: list
        List of discharge objects of QComp
    uncertainty: Uncertainty
        Object of Uncertainty
    initial_settings: dict
        Dictionary of all initial processing settings
    qa: QAData
        Object of QAData
    user_rating: str
        Optional user rating
    comments: list
        List of all user supplied comments
    ext_temp_chk: dict
        Dictionary of external temperature readings
    """

    def __init__(self, in_file, source, proc_type='QRev', checked=False):
        """Initialize instance variables and initiate processing of measurement
        data.

        Parameters
        ----------
        in_file: str or list
            String containing fullname of mmt file for TRDI data, QRev file for
            QRev data, or list of files for SonTek
        source: str
            Source of data. TRDI, SonTek, QRev
        proc_type: str
            Type of processing. QRev, None, Original
        checked: bool
            Boolean to determine if only checked transects should be load for
            TRDI data.
        """

        self.station_name = None
        self.station_number = None
        self.transects = []
        self.mb_tests = []
        self.system_tst = []
        self.compass_cal = []
        self.compass_eval = []
        self.extrap_fit = None
        self.processing = None
        self.discharge = []
        self.uncertainty = None
        self.initial_settings = None
        self.qa = None
        self.user_rating = None
        self.comments = []
        self.ext_temp_chk = {'user': np.nan, 'units': 'C', 'adcp': np.nan}

        # Load data from selected source
        if source == 'QRev':
            self.load_qrev_mat(fullname=in_file)

        else:
            if source == 'TRDI':
                self.load_trdi(in_file, checked=checked)

            elif source == 'SonTek':
                self.load_sontek(in_file)

            # Process TRDI and SonTek data
            if len(self.transects) > 0:

                # Get navigation reference
                # select = self.transects[0].boat_vel.selected
                # if select == 'bt_vel':
                #     ref = 'BT'
                # elif select == 'gga_vel':
                #     ref = 'GGA'
                # elif select == 'vtg_vel':
                #     ref = 'VTG'

                # Process moving-bed tests
                if len(self.mb_tests) > 0:
                    self.mb_tests = MovingBedTests.auto_use_2_correct(
                        moving_bed_tests=self.mb_tests)

                # Save initial settings
                self.initial_settings = self.current_settings()

                # Set processing type
                if proc_type == 'QRev':
                    # Apply QRev default settings
                    settings = self.qrev_default_settings()
                    settings['Processing'] = 'QRev'
                    self.apply_settings(settings)

                elif proc_type == 'None':
                    # Processing with no filters and interpolation
                    settings = self.no_filter_interp_settings()
                    settings['Processing'] = 'None'
                    self.apply_settings(settings)

                elif proc_type == 'Original':
                    # Processing for original settings
                    # from manufacturer software
                    for transect in self.transects:
                        q = QComp()
                        q.populate_data(data_in=transect,
                                        moving_bed_data=self.mb_tests)
                        self.discharge.append(q)
                self.uncertainty = Uncertainty()
                self.uncertainty.compute_uncertainty(self)
                self.qa = QAData(self)

    def load_trdi(self, mmt_file, transect_type='Q', checked=False):
        """Method to load TRDI data.

        Parameters
        ----------
        mmt_file: str
            Full pathname to mmt file.
        transect_type: str
            Type of data (Q: discharge, MB: moving-bed test
        checked: bool
            Determines if all files are loaded (False) or only checked (True)
        """

        # Read mmt file
        mmt = MMT_TRDI(mmt_file)

        # Get properties if they exist, otherwise set them as blank strings
        self.station_name = str(mmt.site_info['Name'])
        self.station_number = str(mmt.site_info['Number'])

        # Initialize processing variable
        self.processing = 'WR2'

        # Create transect objects for  TRDI data
        # TODO refactor allocate_transects
        self.transects = allocate_transects(mmt=mmt,
                                            transect_type=transect_type,
                                            checked=checked)

        # Create object for pre-measurement tests
        if isinstance(mmt.qaqc, dict) or isinstance(mmt.mbt_transects, list):
            self.qaqc_trdi(mmt)
        
        # Save comments from mmt file in comments
        self.comments.append('MMT Remarks: ' + mmt.site_info['Remarks'])

        for t in range(len(self.transects)):
            notes = getattr(mmt.transects[t], 'Notes')
            for note in notes:
                note_text = ' File: ' + note['NoteFileNo'] + ' ' \
                            + note['NoteDate'] + ': ' + note['NoteText']
                self.comments.append(note_text)
                
        # Get external temperature
        if type(mmt.site_info['Water_Temperature']) is float:
            self.ext_temp_chk['user'] = mmt.site_info['Water_Temperature']
            self.ext_temp_chk['units'] = 'C'

        # Initialize thresholds settings dictionary
        threshold_settings = dict()
        threshold_settings['wt_settings'] = {}
        threshold_settings['bt_settings'] = {}
        threshold_settings['depth_settings'] = {}

        # Water track filter threshold settings
        threshold_settings['wt_settings']['beam'] = \
            self.set_num_beam_wt_threshold_trdi(mmt.transects[0])
        threshold_settings['wt_settings']['difference'] = 'Manual'
        threshold_settings['wt_settings']['difference_threshold'] = \
            mmt.transects[0].active_config['Proc_WT_Error_Velocity_Threshold']
        threshold_settings['wt_settings']['vertical'] = 'Manual'
        threshold_settings['wt_settings']['vertical_threshold'] = \
            mmt.transects[0].active_config['Proc_WT_Up_Vel_Threshold']

        # Bottom track filter threshold settings
        threshold_settings['bt_settings']['beam'] = \
            self.set_num_beam_bt_threshold_trdi(mmt.transects[0])
        threshold_settings['bt_settings']['difference'] = 'Manual'
        threshold_settings['bt_settings']['difference_threshold'] = \
            mmt.transects[0].active_config['Proc_BT_Error_Vel_Threshold']
        threshold_settings['bt_settings']['vertical'] = 'Manual'
        threshold_settings['bt_settings']['vertical_threshold'] = \
            mmt.transects[0].active_config['Proc_BT_Up_Vel_Threshold']

        # Depth filter and averaging settings
        threshold_settings['depth_settings']['depth_weighting'] = \
            self.set_depth_weighting_trdi(mmt.transects[0])
        threshold_settings['depth_settings']['depth_valid_method'] = 'TRDI'
        threshold_settings['depth_settings']['depth_screening'] = \
            self.set_depth_screening_trdi(mmt.transects[0])

        # Determine reference used in WR2 if available
        reference = 'BT'
        if 'Reference' in mmt.site_info.keys():
            reference = mmt.site_info['Reference']
            if reference == 'BT':
                target = 'bt_vel'
            elif reference == 'GGA':
                target = 'gga_vel'
            elif reference == 'VTG':
                target = 'vtg_vel'
            for transect in self.transects:
                if getattr(transect.boat_vel, target) is None:
                    reference = 'BT'

        # Convert to earth coordinates
        for transect_idx, transect in enumerate(self.transects):
            # Convert to earth coordinates
            transect.change_coord_sys(new_coord_sys='Earth')

            # Set navigation reference
            transect.change_nav_reference(update=False, new_nav_ref=reference)

            # Apply WR2 thresholds
            self.thresholds_trdi(transect, threshold_settings)

            # Apply boat interpolations
            transect.boat_interpolations(update=False,
                                         target='BT',
                                         method='None')
            if transect.gps is not None:
                transect.boat_interpolations(update=False,
                                             target='GPS',
                                             method='HoldLast')

            # Update water data for changes in boat velocity
            transect.update_water()

            # Filter water data
            transect.w_vel.apply_filter(transect=transect, wt_depth=True)

            # Interpolate water data
            transect.w_vel.apply_interpolation(transect=transect,
                                               ens_interp='None',
                                               cells_interp='None')

            # Apply speed of sound computations as required
            mmt_sos_method = mmt.transects[transect_idx].active_config[
                'Proc_Speed_of_Sound_Correction']

            # Speed of sound computed based on user supplied values
            if mmt_sos_method == 1:
                transect.change_sos(parameter='salinity')
            elif mmt_sos_method == 2:
                # Speed of sound set by user
                speed = mmt.transects[transect_idx].active_config[
                    'Proc_Fixed_Speed_Of_Sound']
                transect.change_sos(parameter='sosSrc',
                                    selected='user',
                                    speed=speed)

    def qaqc_trdi(self, mmt):
        """Processes qaqc test, calibrations, and evaluations
        
        Parameters
        ----------
        mmt: MMT_TRDI
            Object of MMT_TRDI
        """

        # ADCP Test
        if 'RG_Test' in mmt.qaqc:
            for n in range(len(mmt.qaqc['RG_Test'])):
                p_m = PreMeasurement()
                p_m.populate_data(mmt.qaqc['RG_Test_TimeStamp'][n],
                                  mmt.qaqc['RG_Test'][n], 'TST')
                self.system_tst.append(p_m)

        # Compass calibration
        if 'Compass_Calibration' in mmt.qaqc:
            for n in range(len(mmt.qaqc['Compass_Calibration'])):
                cc = PreMeasurement()
                cc.populate_data(mmt.qaqc['Compass_Calibration_TimeStamp'][n],
                                 mmt.qaqc['Compass_Calibration'][n], 'TCC')
                self.compass_cal.append(cc)
        # else:
        #     cc = PreMeasurement()
        #     self.compass_cal.append(cc)
            
        # Compass evaluation
        if 'Compass_Evaluation' in mmt.qaqc:
            for n in range(len(mmt.qaqc['Compass_Evaluation'])):
                ce = PreMeasurement()
                ce.populate_data(mmt.qaqc['Compass_Evaluation_TimeStamp'][n],
                                 mmt.qaqc['Compass_Evaluation'][n], 'TCC')
                self.compass_eval.append(ce)
        # else:
        #     ce = PreMeasurement()
        #     self.compass_cal.append(ce)

        # Check for moving-bed tests
        if len(mmt.mbt_transects) > 0:
            
            # Create transect objects
            transects = allocate_transects(mmt, transect_type='MB')

            # Process moving-bed tests
            if len(transects) > 0:
                self.mb_tests = []
                for n in range(len(transects)):

                    # Create moving-bed test object
                    mb_test = MovingBedTests()
                    mb_test.populate_data('TRDI', transects[n],
                                          mmt.mbt_transects[n].moving_bed_type)
                    
                    # Save notes from mmt files in comments
                    notes = getattr(mmt.mbt_transects[n], 'Notes')
                    for note in notes:
                        note_text = ' File: ' + note['NoteFileNo'] + ' ' \
                                    + note['NoteDate'] + ': ' + note['NoteText']
                        self.comments.append(note_text)

                    self.mb_tests.append(mb_test)

    @staticmethod
    def thresholds_trdi(transect, settings):
        """Retrieve and apply manual filter settings from mmt file

        Parameters
        ----------
        transect: TransectData
            Object of TransectData
        settings: dict
            Threshold settings computed before processing
        """

        # Apply WT settings
        transect.w_vel.apply_filter(transect, **settings['wt_settings'])

        # Apply BT settings
        transect.boat_vel.bt_vel.apply_filter(transect, **settings[
            'bt_settings'])

        # Apply depth settings
        transect.depths.bt_depths.valid_data_method = settings[
            'depth_settings']['depth_valid_method']
        transect.depths.depth_filter(transect=transect, filter_method=settings[
            'depth_settings']['depth_screening'])
        transect.depths.bt_depths.compute_avg_bt_depth(method=settings[
            'depth_settings']['depth_weighting'])

        # Apply composite depths as per setting stored in transect
        # from TransectData
        transect.depths.composite_depths(transect)

    def load_sontek(self, fullnames):
        """Coordinates reading of all SonTek data files.

        Parameters
        ----------
        fullnames: list
            File names including path for all discharge transects converted
            to Matlab files.
        """

        # Initialize variables
        rsdata = None
        pathname = None

        for file in fullnames:
            # Read data file
            rsdata = MatSonTek(file)
            pathname, file_name = os.path.split(file)

            # Create transect objects for each discharge transect
            self.transects.append(TransectData())
            self.transects[-1].sontek(rsdata, file_name)

        # Site information pulled from last file
        if hasattr(rsdata, 'SiteInfo'):
            if hasattr(rsdata.SiteInfo, 'Site_Name'):
                self.station_name = rsdata.SiteInfo.Site_Name
            if hasattr(rsdata.SiteInfo, 'Station_Number'):
                self.station_number = rsdata.SiteInfo.Station_Number

        self.qaqc_sontek(pathname)

        for transect in self.transects:
            transect.change_coord_sys(new_coord_sys='Earth')
            transect.change_nav_reference(
                update=False,
                new_nav_ref=self.transects[0].boat_vel.selected)
            transect.boat_interpolations(update=False,
                                         target='BT',
                                         method='Hold9')
            transect.boat_interpolations(update=False,
                                         target='GPS',
                                         method='None')
            transect.apply_averaging_method(setting='Simple')
            transect.process_depths(update=False,
                                    interpolation_method='HoldLast')
            transect.update_water()

            # Filter water data
            transect.w_vel.apply_filter(transect=transect, wt_depth=True)

            # Interpolate water data
            transect.w_vel.apply_interpolation(transect=transect,
                                               ens_interp='None',
                                               cells_interp='None')
            transect.w_vel.apply_interpolation(transect=transect,
                                               ens_interp='None',
                                               cells_interp='TRDI')

    def qaqc_sontek(self, pathname):
        """Reads and stores system tests, compass calibrations,
        and moving-bed tests.

        Parameters
        ----------
        pathname: str
            Path to discharge transect files.
        """
        # Compass Evaluation
        # ce = PreMeasurement()
        # self.compass_eval.append(ce)

        # Compass Calibration
        compass_cal_folder = os.path.join(pathname, 'CompassCal')
        if os.path.isdir(compass_cal_folder):
            compass_cal_files = []
            for file in os.listdir(compass_cal_folder):

                # G3 compasses
                if file.endswith('.ccal'):
                    # compass_cal_files.append(file)
                    time_stamp = file.split('_')
                    time_stamp = time_stamp[0] + '_' + time_stamp[1]

                # G2 compasses
                elif file.endswith('.txt'):
                    # compass_cal_files.append(file)
                    time_stamp = file.split('l')[1].split('.')[0]

            # for file in compass_cal_files:
                with open(os.path.join(compass_cal_folder, file)) as f:
                    cal_data = f.read()
                    cal = PreMeasurement()
                    cal.populate_data(time_stamp, cal_data, 'SCC')
                    self.compass_cal.append(cal)
        # else:
        #     cal = PreMeasurement()
        #     self.compass_cal.append(cal)

        # System Test
        system_test_folder = os.path.join(pathname, 'SystemTest')
        if os.path.isdir(system_test_folder):
            for file in os.listdir(system_test_folder):
                # Find system test files.
                if file.startswith('SystemTest'):
                    with open(os.path.join(system_test_folder, file)) as f:
                        test_data = f.read()
                        test_data = test_data.replace('\x00', '')
                    time_stamp = file[10:24]
                    sys_test = PreMeasurement()
                    sys_test.populate_data(time_stamp=time_stamp,
                                           data_in=test_data,
                                           data_type='SST')
                    self.system_tst.append(sys_test)

        # Moving-bed tests
        self.sontek_moving_bed_tests(pathname)

    def sontek_moving_bed_tests(self, pathname):
        """Locates and processes SonTek moving-bed tests.

        Searches the pathname for Matlab files that start with Loop or SMBA.
        Processes these files as moving bed tests.

        Parameters
        ----------
        pathname: str
            Path to discharge transect files.
        """
        for file in os.listdir(pathname):
            # Find moving-bed test files.
            if file.endswith('.mat'):
                # Process Loop test
                if file.lower().startswith('loop'):
                    self.mb_tests.append(MovingBedTests())
                    self.mb_tests[-1].populate_data(source='SonTek',
                                                    file=os.path.join(pathname,
                                                                      file),
                                                    test_type='Loop')
                # Process Stationary test
                elif file.lower().startswith('smba'):
                    self.mb_tests.append(MovingBedTests())
                    self.mb_tests[-1].populate_data(source='SonTek',
                                                    file=os.path.join(pathname,
                                                                      file),
                                                    test_type='Stationary')

    def load_qrev_mat(self, fullname):
        """Loads and coordinates the mapping of existing QRev Matlab files
        into Python instance variables.

        Parameters
        ----------
        fullname: str
            Fullname including path to *_QRev.mat files.
        """

        # Read Matlab file and extract meas_struct
        mat_data = sio.loadmat(fullname,
                               struct_as_record=False,
                               squeeze_me=True)

        meas_struct = mat_data['meas_struct']

        # Assign data from meas_struct to associated instance variables
        # in Measurement and associated objects.
        if len(meas_struct.stationName) > 0:
            self.station_name = meas_struct.stationName
        if len(meas_struct.stationNumber) > 0:
            self.station_number = meas_struct.stationNumber
        self.processing = meas_struct.processing
        if type(meas_struct.comments) == np.ndarray:
            self.comments = meas_struct.comments.tolist()
        else:
            self.comments = [meas_struct.comments]
        if hasattr(meas_struct, 'userRating'):
            self.user_rating = meas_struct.userRating
        else:
            self.user_rating = ''

        self.initial_settings = vars(meas_struct.initialSettings)
        # Update initial settings to agree with Python definitions
        if self.initial_settings['NavRef'] == 'btVel':
            self.initial_settings['NavRef'] = 'bt_vel'
        elif self.initial_settings['NavRef'] == 'ggaVel':
            self.initial_settings['NavRef'] = 'gga_vel'
        elif self.initial_settings['NavRef'] == 'vtgVel':
            self.initial_settings['NavRef'] = 'vtg_vel'
        if self.initial_settings['WTwtDepthFilter'] == 'Off':
            self.initial_settings['WTwtDepthFilter'] = False
        elif self.initial_settings['WTwtDepthFilter'] == 'On':
            self.initial_settings['WTwtDepthFilter'] = True
        if type(self.initial_settings['WTsnrFilter']) is np.ndarray:
            self.initial_settings['WTsnrFilter'] = 'Off'
        if self.initial_settings['depthReference'] == 'btDepths':
            self.initial_settings['depthReference'] = 'bt_depths'
        elif self.initial_settings['depthReference'] == 'vbDepths':
            self.initial_settings['depthReference'] = 'vb_depths'
        elif self.initial_settings['depthReference'] == 'ds_Depths':
            self.initial_settings['depthReference'] = 'ds_depths'

        self.ext_temp_chk = {'user': meas_struct.extTempChk.user,
                             'units': meas_struct.extTempChk.units,
                             'adcp': meas_struct.extTempChk.adcp}
        if type(self.ext_temp_chk['user']) is str:
            self.ext_temp_chk['user'] = np.nan
        if type(self.ext_temp_chk['adcp']) is str:
            self.ext_temp_chk['adcp'] = np.nan
        if type(self.ext_temp_chk['user']) is np.ndarray:
            self.ext_temp_chk['user'] = np.nan
        if type(self.ext_temp_chk['adcp']) is np.ndarray:
            self.ext_temp_chk['adcp'] = np.nan

        self.system_tst = PreMeasurement.sys_test_qrev_mat_in(meas_struct)
        # no compass cal compassCal is mat_struct with len(data) = 0
        if type(meas_struct.compassCal) is np.ndarray:
            self.compass_cal = PreMeasurement.cc_qrev_mat_in(meas_struct)
        elif len(meas_struct.compassCal.data) > 0:
            self.compass_cal = PreMeasurement.cc_qrev_mat_in(meas_struct)
        else:
            self.compass_cal = []
        if type(meas_struct.compassEval) is np.ndarray:
            self.compass_eval = PreMeasurement.ce_qrev_mat_in(meas_struct)
        elif len(meas_struct.compassEval.data) > 0:
            self.compass_eval = PreMeasurement.ce_qrev_mat_in(meas_struct)
        else:
            self.compass_eval = []
        self.transects = TransectData.qrev_mat_in(meas_struct)
        self.mb_tests = MovingBedTests.qrev_mat_in(meas_struct)
        self.extrap_fit = ComputeExtrap()
        self.extrap_fit.populate_from_qrev_mat(meas_struct)
        self.discharge = QComp.qrev_mat_in(meas_struct)
        self.uncertainty = Uncertainty()
        self.uncertainty.populate_from_qrev_mat(meas_struct)
        self.qa = QAData(meas_struct, compute=False)

    @staticmethod
    def set_num_beam_wt_threshold_trdi(mmt_transect):
        """Get number of beams to use in processing for WT from mmt file
        
        Parameters
        ----------
        mmt_transect: MMT_Transect
            Object of MMT_Transect
        
        Returns
        -------
        num_3_beam_wt_Out: int
        """

        use_3_beam_wt = mmt_transect.active_config['Proc_Use_3_Beam_WT']
        if use_3_beam_wt == 0:
            num_beam_wt_out = 4
        else:
            num_beam_wt_out = 3
            
        return num_beam_wt_out

    @staticmethod
    def set_num_beam_bt_threshold_trdi(mmt_transect):
        """Get number of beams to use in processing for BT from mmt file

        Parameters
        ----------
        mmt_transect: MMT_Transect
            Object of MMT_Transect

        Returns
        -------
        num_3_beam_WT_Out: int
        """

        use_3_beam_bt = mmt_transect.active_config['Proc_Use_3_Beam_BT']
        if use_3_beam_bt == 0:
            num_beam_bt_out = 4
        else:
            num_beam_bt_out = 3

        return num_beam_bt_out

    @staticmethod
    def set_depth_weighting_trdi(mmt_transect):
        """Get the average depth method from mmt
        
        Parameters
        ----------
        mmt_transect: MMT_Transect
            Object of MMT_Transect
        
        Returns
        -------
        depth_weighting_setting: str
            Method to compute mean depth
        """

        depth_weighting = mmt_transect.active_config['Proc_Use_Weighted_Mean_Depth']
        
        if depth_weighting == 0:
            depth_weighting_setting = 'Simple'
        else:
            depth_weighting_setting = 'IDW'

        return depth_weighting_setting

    @staticmethod
    def set_depth_screening_trdi(mmt_transect):
        """Get the depth screening setting from mmt
        
        Parameters
        ----------
        mmt_transect: MMT_Transect
            Object of MMT_Transect
        
        Returns
        -------
        depth_screening_setting: str
            Type of depth screening to use
        """

        depth_screen = mmt_transect.active_config['Proc_Screen_Depth']
        if depth_screen == 0:
            depth_screening_setting = 'None'
        else:
            depth_screening_setting = 'TRDI'
        
        return depth_screening_setting
        
    def change_sos(self, transect_idx=None, parameter=None, salinity=None,
                   temperature=None, selected=None, speed=None):
        """Applies a change in speed of sound to one or all transects
        and update the discharge and uncertainty computations
        
        Parameters
        ----------
        transect_idx: int
            Index of transect to change
        parameter: str
            Speed of sound parameter to be changed ('temperatureSrc', 'temperature',
            'salinity', 'sosSrc')
        salinity: float
            Salinity in ppt
        temperature: float
            Temperature in deg C
        selected: str
            Selected speed of sound ('internal', 'computed', 'user') or
            temperature ('internal', 'user')
        speed: float
            Manually supplied speed of sound for 'user' source
        """
        
        s = self.current_settings()
        if transect_idx is None:
            # Apply to all transects
            for transect in self.transects:
                transect.change_sos(parameter=parameter,
                                    salinity=salinity,
                                    temperature=temperature,
                                    selected=selected,
                                    speed=speed)
        else:
            # Apply to a single transect
            self.transects[transect_idx].change_sos(parameter=parameter,
                                                    salinity=salinity,
                                                    temperature=temperature,
                                                    selected=selected,
                                                    speed=speed)
        # Reapply settings to newly adjusted data
        self.apply_settings(s)

    def change_magvar(self, magvar, transect_idx=None):
        s = self.current_settings()
        n_transects = len(self.transects)
        recompute = False
        n = 0
        while n <= n_transects and recompute == False:
            if self.transects[n].sensors.heading_deg.selected == 'internal':
                recompute = True
            n += 1

        if transect_idx is None:
            # Apply change to all transects
            for transect in self.transects:
                transect.change_mag_var(magvar)
        else:
            self.transects[transect_idx].change_mag_var(magvar)

        if recompute:
            self.apply_settings(s)

    def change_h_offset(self, h_offset, transect_idx=None):
        s = self.current_settings()
        n_transects = len(self.transects)
        recompute = False
        n = 0
        while n <= n_transects and recompute == False:
            if self.transects[n].sensors.heading_deg.selected == 'internal':
                recompute = True
            n += 1

        if transect_idx is None:
            # Apply change to all transects
            for transect in self.transects:
                transect.change_offset(h_offset)
        else:
            self.transects[transect_idx].change_offset(h_offset)

        if recompute:
            self.apply_settings(s)

    def change_h_source(self, h_source, transect_idx=None):
        s = self.current_settings()
        if transect_idx is None:
            # Apply change to all transects
            for transect in self.transects:
                transect.change_h_source(h_source)
        else:
            self.transects[transect_idx].change_h_source(h_source)

        self.apply_settings(s)

    def change_draft(self, draft, transect_idx=None):
        s = self.current_settings()
        if transect_idx is None:
            # Apply change to all transects
            for transect in self.transects:
                transect.change_draft(draft)
        else:
            self.transects[transect_idx].change_draft(draft)

        self.apply_settings(s)

    @staticmethod
    def h_external_valid(meas):
        external = False
        for transect in meas.transects:
            if transect.sensors.heading_deg.external is not None:
                external = True
                break
        return external

    def apply_settings(self, settings):
        """Applies reference, filter, and interpolation settings.
        
        Parameters
        ----------
        settings: dict
            Dictionary of reference, filter, and interpolation settings
        """

        for transect in self.transects:

            # Moving-boat ensembles
            if 'Processing' in settings.keys():
                transect.change_q_ensembles(proc_method=settings['Processing'])
                self.processing = settings['Processing']

            # Navigation reference
            if transect.boat_vel.selected != settings['NavRef']:
                transect.change_nav_reference(update=False, new_nav_ref=settings['NavRef'])
                if len(self.mb_tests) > 0:
                    self.mb_tests = MovingBedTests.auto_use_2_correct(
                        moving_bed_tests=self.mb_tests,
                        boat_ref=settings['NavRef'])

            # Changing the nav reference applies the current setting for
            # Composite tracks, check to see if a change is needed
            if transect.boat_vel.composite != settings['CompTracks']:
                transect.composite_tracks(update=False, setting=settings['CompTracks'])

            # Set difference velocity BT filter
            bt_kwargs = {}
            if settings['BTdFilter'] == 'Manual':
                bt_kwargs['difference'] = settings['BTdFilter']
                bt_kwargs['difference_threshold'] = settings['BTdFilterThreshold']
            else:
                bt_kwargs['difference'] = settings['BTdFilter']

            # Set vertical velocity BT filter
            if settings['BTwFilter'] == 'Manual':
                bt_kwargs['vertical'] = settings['BTwFilter']
                bt_kwargs['vertical_threshold'] = settings['BTwFilterThreshold']
            else:
                bt_kwargs['vertical'] = settings['BTwFilter']

            # Apply beam filter
                bt_kwargs['beam'] = settings['BTbeamFilter']

            # Apply smooth filter
                bt_kwargs['other'] = settings['BTsmoothFilter']

            # Apply BT settings
            transect.boat_filters(update=False, **bt_kwargs)

            # BT Interpolation
            transect.boat_interpolations(update=False,
                                         target='BT',
                                         method=settings['BTInterpolation'])

            # GPS filter settings
            if transect.gps is not None:
                gga_kwargs = {}
                if transect.boat_vel.gga_vel is not None:
                    # GGA
                    gga_kwargs['differential'] = settings['ggaDiffQualFilter']
                    if settings['ggaAltitudeFilter'] == 'Manual':
                        gga_kwargs['altitude'] = settings['ggaAltitudeFilter']
                        gga_kwargs['altitude_threshold'] = settings['ggaAltitudeFilterChange']
                    else:
                        gga_kwargs['altitude'] = settings['ggaAltitudeFilter']

                    # Set GGA HDOP Filter
                    if settings['GPSHDOPFilter'] == 'Manual':
                        gga_kwargs['hdop'] = settings['GPSHDOPFilter']
                        gga_kwargs['hdop_max_threshold'] = settings['GPSHDOPFilterMax']
                        gga_kwargs['hdop_change_threshold'] = settings['GPSHDOPFilterChange']
                    else:
                        gga_kwargs['hdop'] = settings['GPSHDOPFilter']

                    gga_kwargs['other'] = settings['GPSSmoothFilter']
                    # Apply GGA filters
                    transect.gps_filters(update=False, **gga_kwargs)

                if transect.boat_vel.vtg_vel is not None:
                    vtg_kwargs = {}
                    if settings['GPSHDOPFilter'] == 'Manual':
                        vtg_kwargs['hdop'] = settings['GPSHDOPFilter']
                        vtg_kwargs['hdop_max_threshold'] = settings['GPSHDOPFilterMax']
                        vtg_kwargs['hdop_change_threshold'] = settings['GPSHDOPFilterChange']
                        vtg_kwargs['other'] = settings['GPSSmoothFilter']
                    else:
                        vtg_kwargs['hdop'] = settings['GPSHDOPFilter']
                        vtg_kwargs['other'] = settings['GPSSmoothFilter']

                    # Apply VTG filters
                    transect.gps_filters(update=False, **vtg_kwargs)

                transect.boat_interpolations(update=False,
                                             target='GPS',
                                             method=settings['GPSInterpolation'])

            # Set depth reference
            transect.set_depth_reference(update=False, setting=settings['depthReference'])

            transect.process_depths(update=True,
                                    filter_method=settings['depthFilterType'],
                                    interpolation_method=settings['depthInterpolation'],
                                    composite_setting=settings['depthComposite'],
                                    avg_method=settings['depthAvgMethod'],
                                    valid_method=settings['depthValidMethod'])

            # Set WT difference velocity filter
            wt_kwargs = {}
            if settings['WTdFilter'] == 'Manual':
                wt_kwargs['difference'] = settings['WTdFilter']
                wt_kwargs['difference_threshold'] = settings['WTdFilterThreshold']
            else:
                wt_kwargs['difference'] = settings['WTdFilter']

            # Set WT vertical velocity filter
            if settings['WTwFilter'] == 'Manual':
                wt_kwargs['vertical'] = settings['WTwFilter']
                wt_kwargs['vertical_threshold'] = settings['WTwFilterThreshold']
            else:
                wt_kwargs['vertical'] = settings['WTwFilter']

            wt_kwargs['beam'] = settings['WTbeamFilter']
            wt_kwargs['other'] = settings['WTsmoothFilter']
            wt_kwargs['snr'] = settings['WTsnrFilter']
            wt_kwargs['wt_depth'] = settings['WTwtDepthFilter']
            wt_kwargs['excluded'] = settings['WTExcludedDistance']

            transect.w_vel.apply_filter(transect=transect, **wt_kwargs)

            # Edge methods
            transect.edges.rec_edge_method = settings['edgeRecEdgeMethod']
            transect.edges.vel_method = settings['edgeVelMethod']

        # Recompute extrapolations
        # NOTE: Extrapolations should be determined prior to WT
        # interpolations because the TRDI approach for power/power
        # using the power curve and exponent to estimate invalid cells.

        if self.extrap_fit is None :
            self.extrap_fit = ComputeExtrap()
            self.extrap_fit.populate_data(transects=self.transects, compute_sensitivity=False)
            self.change_extrapolation(self.extrap_fit.fit_method, compute_q=False)
        elif self.extrap_fit.fit_method == 'Automatic':
            self.change_extrapolation(self.extrap_fit.fit_method, compute_q=False)
        else:
            if 'extrapTop' not in settings.keys():
                settings['extrapTop'] = self.extrap_fit.sel_fit[-1].top_method
                settings['extrapBot'] = self.extrap_fit.sel_fit[-1].bot_method
                settings['extrapExp'] = self.extrap_fit.sel_fit[-1].exponent

            self.change_extrapolation(self.extrap_fit.fit_method,
                                      top=settings['extrapTop'],
                                      bot=settings['extrapBot'],
                                      exp=settings['extrapExp'],
                                      compute_q=False)

        for transect in self.transects:

            # Water track interpolations
            transect.w_vel.apply_interpolation(transect=transect,
                                               ens_interp=settings['WTEnsInterpolation'],
                                               cells_interp=settings['WTCellInterpolation'])

        self.extrap_fit.q_sensitivity = ExtrapQSensitivity()
        self.extrap_fit.q_sensitivity.populate_data(transects=self.transects,
                                                    extrap_fits=self.extrap_fit.sel_fit)

        self.compute_discharge()

        self.uncertainty = Uncertainty()
        self.uncertainty.compute_uncertainty(self)
        self.qa = QAData(self)

    def current_settings(self):
        """Saves the current settings for a measurement. Since all settings
        in QRev are consistent among all transects in a measurement only the
        settings from the first transect are saved
        """

        settings = {}
        checked = np.array([x.checked for x in self.transects])
        first_idx = np.where(checked == 1)
        if len(first_idx[0]) == 0:
            first_idx = 0
        else:
            first_idx = first_idx[0][0]

        transect = self.transects[first_idx]
        
        # Navigation reference
        settings['NavRef'] = transect.boat_vel.selected
        
        # Composite tracks
        settings['CompTracks'] = transect.boat_vel.composite
        
        # Water track settings
        settings['WTbeamFilter'] = transect.w_vel.beam_filter
        settings['WTdFilter'] = transect.w_vel.d_filter
        settings['WTdFilterThreshold'] = transect.w_vel.d_filter_threshold
        settings['WTwFilter'] = transect.w_vel.w_filter
        settings['WTwFilterThreshold'] = transect.w_vel.w_filter_threshold
        settings['WTsmoothFilter'] = transect.w_vel.smooth_filter
        settings['WTsnrFilter'] = transect.w_vel.snr_filter
        settings['WTwtDepthFilter'] = transect.w_vel.wt_depth_filter
        settings['WTEnsInterpolation'] = transect.w_vel.interpolate_ens
        settings['WTCellInterpolation'] = transect.w_vel.interpolate_cells
        settings['WTExcludedDistance'] = transect.w_vel.excluded_dist_m
        
        # Bottom track settings
        settings['BTbeamFilter'] = self.transects[first_idx].boat_vel.bt_vel.beam_filter
        settings['BTdFilter'] = self.transects[first_idx].boat_vel.bt_vel.d_filter
        settings['BTdFilterThreshold'] = \
            self.transects[first_idx].boat_vel.bt_vel.d_filter_threshold
        settings['BTwFilter'] = self.transects[first_idx].boat_vel.bt_vel.w_filter
        settings['BTwFilterThreshold'] = \
            self.transects[first_idx].boat_vel.bt_vel.w_filter_threshold
        settings['BTsmoothFilter'] = self.transects[first_idx].boat_vel.bt_vel.smooth_filter
        settings['BTInterpolation'] = self.transects[first_idx].boat_vel.bt_vel.interpolate
        
        # Gps Settings
        if transect.gps is not None:

            # GGA settings
            if transect.boat_vel.gga_vel is not None:
                settings['ggaDiffQualFilter'] = transect.boat_vel.gga_vel.gps_diff_qual_filter
                settings['ggaAltitudeFilter'] = transect.boat_vel.gga_vel.gps_altitude_filter
                settings['ggaAltitudeFilterChange'] = \
                    transect.boat_vel.gga_vel.gps_altitude_filter_change
                settings['GPSHDOPFilter'] = transect.boat_vel.gga_vel.gps_HDOP_filter
                settings['GPSHDOPFilterMax'] = transect.boat_vel.gga_vel.gps_HDOP_filter_max
                settings['GPSHDOPFilterChange'] = transect.boat_vel.gga_vel.gps_HDOP_filter_change
                settings['GPSSmoothFilter'] = transect.boat_vel.gga_vel.smooth_filter
                settings['GPSInterpolation'] = transect.boat_vel.gga_vel.interpolate
            else:
                settings['ggaDiffQualFilter'] = 1
                settings['ggaAltitudeFilter'] = 'Off'
                settings['ggaAltitudeFilterChange'] = []
                
                settings['ggaSmoothFilter'] = 'Off'
                if 'GPSInterpolation' not in settings.keys():
                    settings['GPSInterpolation'] = 'None'
                if 'GPSHDOPFilter' not in settings.keys():
                    settings['GPSHDOPFilter'] = 'Off'
                    settings['GPSHDOPFilterMax'] = []
                    settings['GPSHDOPFilterChange'] = []
                if 'GPSSmoothFilter' not in settings.keys():
                    settings['GPSSmoothFilter'] = 'Off'

        # VTG settings
        if transect.boat_vel.vtg_vel is not None:
            settings['GPSHDOPFilter'] = transect.boat_vel.vtg_vel.gps_HDOP_filter
            settings['GPSHDOPFilterMax'] = transect.boat_vel.vtg_vel.gps_HDOP_filter_max
            settings['GPSHDOPFilterChange'] = transect.boat_vel.vtg_vel.gps_HDOP_filter_change
            settings['GPSSmoothFilter'] = transect.boat_vel.vtg_vel.smooth_filter
            settings['GPSInterpolation'] = transect.boat_vel.vtg_vel.interpolate
        else:
            settings['vtgSmoothFilter'] = 'Off'
            if 'GPSInterpolation' not in settings.keys():
                settings['GPSInterpolation'] = 'None'
            if 'GPSHDOPFilter' not in settings.keys():
                settings['GPSHDOPFilter'] = 'Off'
                settings['GPSHDOPFilterMax'] = []
                settings['GPSHDOPFilterChange'] = []
            if 'GPSSmoothFilter' not in settings.keys():
                settings['GPSSmoothFilter'] = 'Off'
                    
        # Depth Settings
        settings['depthAvgMethod'] = transect.depths.bt_depths.avg_method
        settings['depthValidMethod'] = transect.depths.bt_depths.valid_data_method
        
        # Depth settings are always applied to all available depth sources.
        # Only those saved in the bt_depths are used here but are applied to all sources
        settings['depthFilterType'] = transect.depths.bt_depths.filter_type
        settings['depthReference'] = transect.depths.selected
        settings['depthComposite'] = transect.depths.composite
        select = getattr(transect.depths, transect.depths.selected)
        settings['depthInterpolation'] = select.interp_type
        
        # Extrap Settings
        if self.extrap_fit is None:
            settings['extrapTop'] = transect.extrap.top_method
            settings['extrapBot'] = transect.extrap.bot_method
            settings['extrapExp'] = transect.extrap.exponent
        else:
            settings['extrapTop'] = self.extrap_fit.sel_fit[-1].top_method
            settings['extrapBot'] = self.extrap_fit.sel_fit[-1].bot_method
            settings['extrapExp'] = self.extrap_fit.sel_fit[-1].exponent
        
        # Edge Settings
        settings['edgeVelMethod'] = transect.edges.vel_method
        settings['edgeRecEdgeMethod'] = transect.edges.rec_edge_method
        
        return settings

    def qrev_default_settings(self):
        """QRev default and filter settings for a measurement"""

        settings = dict()

        # Navigation reference
        settings['NavRef'] = self.transects[0].boat_vel.selected

        # Composite tracks
        settings['CompTracks'] = 'Off'

        # Water track filter settings
        settings['WTbeamFilter'] = -1
        settings['WTdFilter'] = 'Auto'
        settings['WTdFilterThreshold'] = np.nan
        settings['WTwFilter'] = 'Auto'
        settings['WTwFilterThreshold'] = np.nan
        settings['WTsmoothFilter'] = 'Off'
        if self.transects[0].adcp.manufacturer == 'TRDI':
            settings['WTsnrFilter'] = 'Off'
        else:
            settings['WTsnrFilter'] = 'Auto'
        temp = [x.w_vel for x in self.transects]
        excluded_dist = np.nanmin([x.excluded_dist_m for x in temp])
        if excluded_dist < 0.158 and self.transects[0].adcp.model == 'M9':
            settings['WTExcludedDistance'] = 0.16
        else:
            settings['WTExcludedDistance'] = excluded_dist

        # Bottom track filter settings
        settings['BTbeamFilter'] = -1
        settings['BTdFilter'] = 'Auto'
        settings['BTdFilterThreshold'] = np.nan
        settings['BTwFilter'] = 'Auto'
        settings['BTwFilterThreshold'] = np.nan
        settings['BTsmoothFilter'] = 'Off'

        # GGA Filter settings
        settings['ggaDiffQualFilter'] = 2
        settings['ggaAltitudeFilter'] = 'Auto'
        settings['ggaAltitudeFilterChange'] = np.nan

        # VTG filter settings
        settings['vtgsmoothFilter'] = 'Off'

        # GGA and VTG filter settings
        settings['GPSHDOPFilter'] = 'Auto'
        settings['GPSHDOPFilterMax'] = np.nan
        settings['GPSHDOPFilterChange'] = np.nan
        settings['GPSSmoothFilter'] = 'Off'

        # Depth Averaging
        settings['depthAvgMethod'] = 'IDW'
        settings['depthValidMethod'] = 'QRev'

        # Depth Reference

        # Default to 4 beam depth average
        settings['depthReference'] = 'bt_depths'
        # Depth settings
        settings['depthFilterType'] = 'Smooth'
        for transect in self.transects:
            if transect.checked:

                if transect.depths.vb_depths is not None or transect.depths.ds_depths is not None:
                    settings['depthComposite'] = 'On'
                    break
                else:
                    settings['depthComposite'] = 'Off'
                    break


        # Interpolation settings
        settings = self.qrev_default_interpolation_methods(settings)

        # Edge settings
        settings['edgeVelMethod'] = 'MeasMag'
        settings['edgeRecEdgeMethod'] = 'Fixed'

        return settings

    def no_filter_interp_settings(self):
        """Settings to turn off all filters and interpolations.

        Returns
        -------
        settings: dict
            Dictionary of all processing settings.
        """

        settings = dict()

        settings['NavRef'] = self.transects[0].boatVel.selected

        # Composite tracks
        settings['CompTracks'] = 'Off'

        # Water track filter settings
        settings['WTbeamFilter'] = 3
        settings['WTdFilter'] = 'Off'
        settings['WTdFilterThreshold'] = np.nan
        settings['WTwFilter'] = 'Off'
        settings['WTwFilterThreshold'] = np.nan
        settings['WTsmoothFilter'] = 'Off'
        settings['WTsnrFilter'] = 'Off'

        temp = [x.w_vel for x in self.transects]
        excluded_dist = np.nanmin([x.excluded_dist_m for x in temp])

        settings['WTExcludedDistance'] = excluded_dist

        # Bottom track filter settings
        settings['BTbeamFilter'] = 3
        settings['BTdFilter'] = 'Off'
        settings['BTdFilterThreshold'] = np.nan
        settings['BTwFilter'] = 'Off'
        settings['BTwFilterThreshold'] = np.nan
        settings['BTsmoothFilter'] = 'Off'

        # GGA filter settings
        settings['ggaDiffQualFilter'] = 1
        settings['ggaAltitudeFilter'] = 'Off'
        settings['ggaAltitudeFilterChange'] = np.nan

        # VTG filter settings
        settings['vtgsmoothFilter'] = 'Off'

        # GGA and VTG filter settings
        settings['GPSHDOPFilter'] = 'Off'
        settings['GPSHDOPFilterMax'] = np.nan
        settings['GPSHDOPFilterChange'] = np.nan
        settings['GPSSmoothFilter'] = 'Off'

        # Depth Averaging
        settings['depthAvgMethod'] = 'IDW'
        settings['depthValidMethod'] = 'QRev'

        # Depth Reference

        # Default to 4 beam depth average
        settings['depthReference'] = 'btDepths'
        # Depth settings
        settings['depthFilterType'] = 'None'
        settings['depthComposite'] = 'Off'

        # Interpolation settings
        settings['BTInterpolation'] = 'None'
        settings['WTEnsInterpolation'] = 'None'
        settings['WTCellInterpolation'] = 'None'
        settings['GPSInterpolation'] = 'None'
        settings['depthInterpolation'] = 'None'
        settings['WTwtDepthFilter'] = 'Off'

        # Edge Settings
        settings['edgeVelMethod'] = 'MeasMag'
        # settings['edgeVelMethod'] = 'Profile'
        settings['edgeRecEdgeMethod'] = 'Fixed'

        return settings

    def selected_transects_changed(self, selected_transects_idx):

        for n in range(len(self.transects)):
            if n in selected_transects_idx:
                self.transects[n].checked = True
            else:
                self.transects[n].checked = False
        # Recompute extrapolations
        # NOTE: Extrapolations should be determined prior to WT
        # interpolations because the TRDI approach for power/power
        # using the power curve and exponent to estimate invalid cells.

        if (self.extrap_fit is None) or (self.extrap_fit.fit_method == 'Automatic'):
            self.extrap_fit = ComputeExtrap()
            self.extrap_fit.populate_data(transects=self.transects, compute_sensitivity=False)
            top = self.extrap_fit.sel_fit[-1].top_method
            bot = self.extrap_fit.sel_fit[-1].bot_method
            exp = self.extrap_fit.sel_fit[-1].exponent
            self.change_extrapolation(self.extrap_fit.fit_method, top=top, bot=bot, exp=exp)

        self.extrap_fit.q_sensitivity = ExtrapQSensitivity()
        self.extrap_fit.q_sensitivity.populate_data(transects=self.transects,
                                                    extrap_fits=self.extrap_fit.sel_fit)

        self.compute_discharge()
        self.uncertainty = Uncertainty()
        self.uncertainty.compute_uncertainty(self)
        self.qa = QAData(self)

    def compute_discharge(self):
        self.discharge = []
        for transect in self.transects:
            q = QComp()
            q.populate_data(data_in=transect, moving_bed_data=self.mb_tests)
            self.discharge.append(q)

    @staticmethod
    def qrev_default_interpolation_methods(settings):
        """Adds QRev default interpolation settings to existing settings data structure

        Parameters
        ----------
        settings: dict
            Dictionary of reference and filter settings

        Returns
        -------
        settings: dict
            Dictionary with reference, filter, and interpolation settings
        """

        settings['BTInterpolation'] = 'Linear'
        settings['WTEnsInterpolation'] = 'abba'
        settings['WTCellInterpolation'] = 'abba'
        settings['GPSInterpolation'] = 'Linear'
        settings['depthInterpolation'] = 'Linear'
        settings['WTwtDepthFilter'] = 'On'

        return settings

    def change_extrapolation(self, method, top=None, bot=None,
                             exp=None, extents=None, threshold=None, compute_q=True):
        """Applies the selected extrapolation method to each transect.

        Parameters
        ----------
        method: str
            Method of computation Automatic or Manual
        top: str
            Top extrapolation method
        bot: str
            Bottom extrapolation method
        exp: float
            Exponent for power or no slip methods
        threshold: float
            Threshold as a percent for determining if a median is valid
        extents: list
            Percent of discharge, does not account for transect direction
        """

        if top is None:
            top = self.extrap_fit.sel_fit[-1].top_method
        if bot is None:
            bot = self.extrap_fit.sel_fit[-1].bot_method
        if exp is None:
            exp = self.extrap_fit.sel_fit[-1].exponent
        if extents is not None:
            self.extrap_fit.subsection = extents
        if threshold is not None:
            self.extrap_fit.threshold = threshold

        data_type = self.extrap_fit.norm_data[-1].data_type
        if data_type is None:
            data_type = 'q'

        if method == 'Manual':
            self.extrap_fit.fit_method = 'Manual'
            for transect in self.transects:
                transect.extrap.set_extrap_data(top=top, bot=bot, exp=exp)
            self.extrap_fit.process_profiles(transects=self.transects, data_type=data_type)
        else:
            self.extrap_fit.fit_method = 'Automatic'
            self.extrap_fit.process_profiles(transects=self.transects, data_type=data_type)
            for transect in self.transects:
                transect.extrap.set_extrap_data(top=self.extrap_fit.sel_fit[-1].top_method,
                                                bot=self.extrap_fit.sel_fit[-1].bot_method,
                                                exp=self.extrap_fit.sel_fit[-1].exponent)

        if compute_q:
            self.extrap_fit.q_sensitivity = ExtrapQSensitivity()
            self.extrap_fit.q_sensitivity.populate_data(transects=self.transects,
                                                        extrap_fits=self.extrap_fit.sel_fit)

            self.compute_discharge()

    @staticmethod
    def measurement_duration(self):
        duration = 0
        for transect in self.transects:
            if transect.checked:
                duration += transect.date_time.transect_duration_sec
        return duration

    @staticmethod
    def mean_discharges(self):

        total_q = []
        uncorrected_q = []
        top_q = []
        bot_q = []
        mid_q = []
        left_q = []
        right_q = []
        int_cells_q = []
        int_ensembles_q = []

        for n, transect in enumerate(self.transects):
            if transect.checked:
                total_q.append(self.discharge[n].total)
                uncorrected_q.append(self.discharge[n].total_uncorrected)
                top_q.append(self.discharge[n].top)
                mid_q.append(self.discharge[n].middle)
                bot_q.append(self.discharge[n].bottom)
                left_q.append(self.discharge[n].left)
                right_q.append(self.discharge[n].right)
                int_cells_q.append(self.discharge[n].int_cells)
                int_ensembles_q.append(self.discharge[n].int_ens)

        discharge = {'total_mean': np.mean(total_q),
                     'uncorrected_mean': np.mean(uncorrected_q),
                     'top_mean': np.mean(top_q),
                     'mid_mean': np.mean(mid_q),
                     'bot_mean': np.mean(bot_q),
                     'left_mean': np.mean(left_q),
                     'right_mean': np.mean(right_q),
                     'int_cells_mean': np.mean(int_cells_q),
                     'int_ensembles_mean': np.mean(int_ensembles_q)}

        return discharge

    @staticmethod
    def save_matlab_file(self, file_name):

        from Classes.Python2Matlab import Python2Matlab
        dsm_struct = {'dsm_struct': Python2Matlab(self).matlab_dict}
        sio.savemat(file_name='C:/dsm/dsm_downloads/dsm_mat_test.mat',
                    mdict=dsm_struct,
                    appendmat=True,
                    format='5',
                    long_field_names=True,
                    do_compression=False,
                    oned_as='row')

    @staticmethod
    def compute_measurement_properties(self):
        """Computes characteristics of the measurement that assist in evaluating the consistency of the transects.

        Returns
        -------
        trans_prop: dict
        Dictionary of transect properties
            width: float
                width in m
            width_cov: float
                coefficient of variation of width in percent
            area: float
                cross sectional area in m**2
            area_cov: float
                coefficient of variation of are in percent
            avg_boat_speed: float
                average boat speed in mps
            avg_boat_course: float
                average boat course in degrees
            avg_water_speed: float
                average water speed in mps
            avg_water_dir: float
                average water direction in degrees
            avg_depth: float
                average depth in m
            max_depth: float
                maximum depth in m
            max_water_speed: float
                99th percentile of water speed in mps
        """

        checked_idx = np.array([], dtype=int)
        n_transects = len(self.transects)
        trans_prop = {'width': np.array([np.nan] * (n_transects + 1)),
                      'width_cov': np.array([np.nan] * (n_transects + 1)),
                      'area': np.array([np.nan] * (n_transects + 1)),
                      'area_cov': np.array([np.nan] * (n_transects + 1)),
                      'avg_boat_speed': np.array([np.nan] * (n_transects + 1)),
                      'avg_boat_course': np.array([np.nan] * (n_transects)),
                      'avg_water_speed': np.array([np.nan] * (n_transects + 1)),
                      'avg_water_dir': np.array([np.nan] * (n_transects + 1)),
                      'avg_depth': np.array([np.nan] * (n_transects + 1)),
                      'max_depth': np.array([np.nan] * (n_transects + 1)),
                      'max_water_speed': np.array([np.nan] * (n_transects + 1))}

        for n, transect in enumerate(self.transects):

            # Compute boat track properties
            boat_track = BoatStructure.compute_boat_track(transect)

            # Get boat speeds
            in_transect_idx = transect.in_transect_idx
            if getattr(transect.boat_vel, transect.boat_vel.selected) is not None:
                boat_selected = getattr(transect.boat_vel, transect.boat_vel.selected)
                u_boat = boat_selected.u_processed_mps[in_transect_idx]
                v_boat = boat_selected.v_processed_mps[in_transect_idx]
            else:
                u_boat = nans(transect.boat_vel.bt_vel.u_processed_mps[in_transect_idx].shape)
                v_boat = nans(transect.boat_vel.bt_vel.v_processed_mps[in_transect_idx].shape)

            if np.logical_not(np.all(np.isnan(boat_track['track_x_m']))):

                # Compute boat course and mean speed
                [course_radians, dmg] = cart2pol(boat_track['track_x_m'][-1], boat_track['track_y_m'][-1])
                trans_prop['avg_boat_course'][n] = rad2azdeg(course_radians)
                trans_prop['avg_boat_speed'][n] = np.nanmean(np.sqrt(u_boat**2 + v_boat**2))

                # Compute width
                trans_prop['width'][n] = np.nansum([dmg, transect.edges.left.distance_m,
                                                    transect.edges.right.distance_m])

                # Project the shiptrack onto a line from the beginning to end of the transect
                unit_x, unit_y = pol2cart(course_radians, 1)
                bt = np.array([boat_track['track_x_m'], boat_track['track_y_m']]).T
                dot_prod = bt @ np.array([unit_x, unit_y])
                projected_x = dot_prod * unit_x
                projected_y = dot_prod * unit_y
                station = np.sqrt(projected_x**2 + projected_y**2)

                # Get selected depth object
                depth = getattr(transect.depths, transect.depths.selected)
                depth_a = np.copy(depth.depth_processed_m)
                depth_a[np.isnan(depth_a)] = 0
                # Compute area of the moving-boat portion of the cross section using trapezoidal integration.
                # This method is consistent with AreaComp but is different from QRev in Matlab
                area_moving_boat = np.abs(np.trapz(depth_a[in_transect_idx], station[in_transect_idx]))

                # Compute area of left edge
                edge_type = transect.edges.left.type
                coef = 1
                if edge_type == 'Triangular':
                    coef = 0.5
                elif edge_type == 'Rectangular':
                    coef = 1.0
                elif edge_type == 'Custom':
                    coef = 0.5 + (transect.edges.left.cust_coef - 0.3535)
                elif edge_type == 'User Q':
                    coef = 0.5
                edge_idx = QComp.edge_ensembles('left', transect)
                edge_depth = np.nanmean(depth.depth_processed_m[edge_idx])
                area_left = edge_depth * transect.edges.left.distance_m * coef

                # Compute area of right edge
                edge_type = transect.edges.right.type
                if edge_type == 'Triangular':
                    coef = 0.5
                elif edge_type == 'Rectangular':
                    coef = 1.0
                elif edge_type == 'Custom':
                    coef = 0.5 + (transect.edges.right.cust_coef - 0.3535)
                elif edge_type == 'User Q':
                    coef = 0.5
                edge_idx = QComp.edge_ensembles('right', transect)
                edge_depth = np.nanmean(depth.depth_processed_m[edge_idx])
                area_right = edge_depth * transect.edges.right.distance_m * coef

                # Compute total cross sectional area
                trans_prop['area'][n] = np.nansum([area_left, area_moving_boat, area_right])

                # Compute average water speed
                trans_prop['avg_water_speed'][n] = self.discharge[n].total / trans_prop['area'][n]

                # Compute flow direction using discharge weighting
                u_water = transect.w_vel.u_processed_mps[:, in_transect_idx]
                v_water = transect.w_vel.v_processed_mps[:, in_transect_idx]
                weight = np.abs(self.discharge[n].middle_cells)
                u = np.nansum(np.nansum(u_water * weight)) / np.nansum(np.nansum(weight))
                v = np.nansum(np.nansum(v_water * weight)) / np.nansum(np.nansum(weight))
                trans_prop['avg_water_dir'][n] = np.arctan2(u, v) * 180 / np.pi
                if trans_prop['avg_water_dir'][n] < 0:
                    trans_prop['avg_water_dir'][n] = trans_prop['avg_water_dir'][n] + 360

                # Compute average and max depth
                # This is a deviation from QRev in Matlab which simply averaged all the depths
                trans_prop['avg_depth'][n] = trans_prop['area'][n] / trans_prop['width'][n]
                trans_prop['max_depth'][n] = np.nanmax(depth.depth_processed_m[in_transect_idx])

                # Compute max water speed using the 99th percentile
                water_speed = np.sqrt(u_water**2 + v_water**2)
                trans_prop['max_water_speed'][n] = np.nanpercentile(water_speed, 99)
                if transect.checked:
                    checked_idx = np.append(checked_idx, n)

            # Only transects used for discharge are included in measurement properties
            if len(checked_idx) > 0:
                n = n_transects
                trans_prop['width'][n] = np.nanmean(trans_prop['width'][checked_idx])
                trans_prop['width_cov'][n] = (np.nanstd(trans_prop['width'][checked_idx], ddof=1) /
                                              trans_prop['width'][n]) * 100
                trans_prop['area'][n] = np.nanmean(trans_prop['area'][checked_idx])
                trans_prop['area_cov'][n] = (np.nanstd(trans_prop['area'][checked_idx], ddof=1) /
                                             trans_prop['area'][n]) * 100
                trans_prop['avg_boat_speed'][n] = np.nanmean(trans_prop['avg_boat_speed'][checked_idx])
                trans_prop['avg_water_speed'][n] = np.nanmean(trans_prop['avg_water_speed'][checked_idx])
                trans_prop['avg_depth'][n] = np.nanmean(trans_prop['avg_depth'][checked_idx])
                trans_prop['max_depth'][n] = np.nanmax(trans_prop['max_depth'][checked_idx])
                trans_prop['max_water_speed'][n] = np.nanmax(trans_prop['max_water_speed'][checked_idx])

                # Compute average water direction using vector coordinates to avoid the problem of averaging
                # fluctuations that cross zero degrees
                x_coord = []
                y_coord = []
                for idx in checked_idx:
                    water_dir_rad = azdeg2rad(trans_prop['avg_water_dir'][idx])
                    x, y = pol2cart(water_dir_rad, 1)
                    x_coord.append(x)
                    y_coord.append(y)
                avg_water_dir_rad, _ = cart2pol(np.mean(x_coord), np.mean(y_coord))
                trans_prop['avg_water_dir'][n] = rad2azdeg(avg_water_dir_rad)

        return trans_prop

    @staticmethod
    def checked_transects(meas):
        checked_transect_idx = []
        for n in range(len(meas.transects)):
            if meas.transects[n].checked:
                checked_transect_idx.append(n)
        return checked_transect_idx

    @staticmethod
    def compute_time_series(meas, variable=None):

        data = np.array([])
        serial_time = np.array([])
        idx_transects = Measurement.checked_transects(meas)
        for idx in idx_transects:
            if variable == 'Temperature':
                data = np.append(data, meas.transects[idx].sensors.temperature_deg_c.internal.data)
            ens_cum_time = np.nancumsum(meas.transects[idx].date_time.ens_duration_sec)
            ens_time = meas.transects[idx].date_time.start_serial_time + ens_cum_time
            serial_time = np.append(serial_time, ens_time)
        return data, serial_time

    def xml_output(self, version, file_name):
        channel = ET.Element('Channel', QRevFilename=os.path.basename(file_name[:-4]), QRevVersion=version)

        # (2) SiteInformation Node
        if self.station_name or self.station_number:
            site_info = ET.SubElement(channel, 'SiteInformation')

            # (3) StationName Node
            if self.station_name:
                ET.SubElement(site_info, 'StationName', type='char').text = self.station_name

            # (3) SiteID Node
            if self.station_number and int(self.station_number) != 0:
                ET.SubElement(site_info, 'SiteID', type='char').text = self.station_number

        # (2) QA Node
        qa = ET.SubElement(channel, 'QA')

        # (3) DiagnosticTestResult Node
        if len(self.system_tst) > 0:
            last_test = self.system_tst[-1].data
            failed_idx = last_test.count('FAIL')
            if failed_idx == 0:
                test_result = 'Pass'
            else:
                test_result = str(failed_idx) + ' Failed'
        else:
            test_result = 'None'
        ET.SubElement(qa, 'DiagnosticTestResult', type='char').text = test_result

        # (3) CompassCalibrationResult Node
        try:
            last_eval = self.compass_eval[-1]
            # StreamPro, RR
            idx = last_eval.data.find('Typical Heading Error: <')
            if idx == (-1):
                # Rio Grande
                idx = last_eval.data.find('>>> Total error:')
                if idx != (-1):
                    idx_start = idx + 17
                    idx_end = idx_start + 10
                    comp_error = last_eval.data[idx_start:idx_end]
                    comp_error = ''.join([n for n in comp_error if n.isdigit() or n == '.'])
                else:
                    comp_error = ''
            else:
                # StreamPro, RR
                idx_start = idx + 24
                idx_end = idx_start + 10
                comp_error = last_eval.data[idx_start:idx_end]
                comp_error = ''.join([n for n in comp_error if n.isdigit() or n == '.'])
            # Evaluation could not be determined
            if not comp_error:
                ET.SubElement(qa, 'CompassCalibrationResult', type='char').text = 'Yes'
            elif comp_error == '':
                ET.SubElement(qa, 'CompassCalibrationResult', type='char').text = 'No'
            else:
                ET.SubElement(qa, 'CompassCalibrationResult', type='char').text = 'Max ' + comp_error
        except (IndexError, TypeError, AttributeError):
            try:
                last_eval = self.compass_eval[-1]
                ET.SubElement(qa, 'CompassCalibrationResult', type='char').text = 'Yes'
            except (IndexError, TypeError):
                ET.SubElement(qa, 'CompassCalibrationResult', type='char').text = 'No'

        # (3) MovingBedTestType Node
        if not self.mb_tests:
            ET.SubElement(qa, 'MovingBedTestType', type='char').text = 'None'
        else:
            selected_idx = [i for (i, val) in enumerate(self.mb_tests) if val.selected is True]
            if len(selected_idx) >= 1:
                temp = self.mb_tests[selected_idx[0]].type
            else:
                temp = self.mb_tests[selected_idx[-1]].type
            ET.SubElement(qa, 'MovingBedTestType', type='char').text = str(temp)

            # MovingBedTestResult Node
            temp = 'Unknown'
            for idx in selected_idx:
                if self.mb_tests[idx].moving_bed == 'Yes':
                    temp = 'Yes'
                    break
                elif self.mb_tests[idx].moving_bed == 'No':
                    temp = 'No'

            ET.SubElement(qa, 'MovingBedTestResult', type='char').text = temp

        # (3) DiagnosticTest and Text Node
        if self.system_tst:
            test_text = ''
            for test in self.system_tst:
                test_text += test.data
            diag_test = ET.SubElement(qa, 'DiagnosticTest')
            ET.SubElement(diag_test, 'Text', type='char').text = test_text

        # (3) CompassCalibration and Text Node
        compass_text = ''
        try:
            for each in self.compass_cal:
                if self.transects[0].adcp.manufacturer == 'SonTek':
                    idx = each.data.find('CAL_TIME')
                    compass_text += each.data[idx:]
                else:
                    compass_text += each.data
        except (IndexError, TypeError, AttributeError):
            pass
        try:
            for each in self.compass_eval:
                if self.transects[0].adcp.manufacturer == 'SonTek':
                    idx = each.data.find('CAL_TIME')
                    compass_text += each.data[idx:]
                else:
                    compass_text += each.data
        except (IndexError, TypeError, AttributeError):
            pass

        if len(compass_text) > 0:
            comp_cal = ET.SubElement(qa, 'CompassCalibration')
            ET.SubElement(comp_cal, 'Text', type='char').text = compass_text

        # (3) MovingBedTest Node
        if self.mb_tests:
            for each in self.mb_tests:
                mbt = ET.SubElement(qa, 'MovingBedTest')

                # (4) Filename Node
                ET.SubElement(mbt, 'Filename', type='char').text = each.transect.file_name

                # (4) TestType Node
                ET.SubElement(mbt, 'TestType', type='char').text = each.type

                # (4) Duration Node
                ET.SubElement(mbt, 'Duration', type='double', unitsCode='sec').text = '{:.2f}'.format(each.duration_sec)

                # (4) PercentInvalidBT Node
                ET.SubElement(mbt, 'PercentInvalidBT', type='double').text = '{:.4f}'.format(each.percent_invalid_bt)

                # (4) HeadingDifference Node
                if each.compass_diff_deg:
                    temp = '{:.2f}'.format(each.compass_diff_deg)
                else:
                    temp =''
                    ET.SubElement(mbt, 'HeadingDifference', type='double', unitsCode='deg').text = temp

                # (4) MeanFlowDirection Node
                if each.flow_dir:
                    temp = '{:.2f}'.format(each.flow_dir)
                else:
                    temp = ''
                ET.SubElement(mbt, 'MeanFlowDirection', type='double', unitsCode='deg').text = temp

                # (4) MovingBedDirection Node
                if each.mb_dir:
                    temp = '{:.2f}'.format(each.mb_dir)
                else:
                    temp = ''
                ET.SubElement(mbt, 'MovingBedDirection', type='double', unitsCode='deg').text = temp

                # (4) DistanceUpstream Node
                ET.SubElement(mbt, 'DistanceUpstream', type='double', unitsCode='m').text = \
                    '{:.4f}'.format(each.dist_us_m)

                # (4) MeanFlowSpeed Node
                ET.SubElement(mbt, 'MeanFlowSpeed', type='double', unitsCode='mps').text = \
                    '{:.4f}'.format(each.flow_spd_mps)

                # (4) MovingBedSpeed Node
                ET.SubElement(mbt, 'MovingBedSpeed', type='double', unitsCode='mps').text = \
                    '{:.4f}'.format(each.mb_spd_mps)

                # (4) PercentMovingBed Node
                ET.SubElement(mbt, 'PercentMovingBed', type='double').text = '{:.2f}'.format(each.percent_mb)

                # (4) TestQuality Node
                ET.SubElement(mbt, 'TestQuality', type='char').text = each.test_quality

                # (4) MovingBedPresent Node
                ET.SubElement(mbt, 'MovingBedPresent', type='char').text = each.moving_bed

                # (4) UseToCorrect Node
                if each.use_2_correct:
                    ET.SubElement(mbt, 'UseToCorrect', type='char').text = 'Yes'
                else:
                    ET.SubElement(mbt, 'UseToCorrect', type='char').text = 'No'

                # (4) UserValid Node
                if each.user_valid:
                    ET.SubElement(mbt, 'UserValid', type='char').text = 'Yes'
                else:
                    ET.SubElement(mbt, 'UserValid', type='char').text = 'No'

                # (4) Message Node
                if len(each.messages) > 0:
                    str_out = ''
                    for message in each.messages:
                            str_out = str_out + message + '; '
                    ET.SubElement(mbt, 'Message', type='char').text = str_out

        # (3) TemperatureCheck Node
        temp_check = ET.SubElement(qa, 'TemperatureCheck')

        # (4) VerificationTemperature Node
        if not np.isnan(self.ext_temp_chk['user']):
            ET.SubElement(temp_check, 'VerificationTemperature', type='double', unitsCode='degC').text = \
                '{:.2f}'.format(self.ext_temp_chk['user'])

        # (4) InstrumentTemperature Node
        if not np.isnan(self.ext_temp_chk['adcp']):
            ET.SubElement(temp_check, 'InstrumentTemperature', type='double', unitsCode='degC').text = '{:.2f}'.format(
                self.ext_temp_chk['adcp'])

        # (4) TemperatureChange Node:
        temp_all = np.array([np.nan])
        for each in self.transects:
            # Check for situation where user has entered a constant temperature
            temperature_selected = getattr(each.sensors.temperature_deg_c, each.sensors.temperature_deg_c.selected)
            temperature = temperature_selected.data
            if len(temperature) > 1:
                # Temperatures for ADCP.
                temp_all = np.concatenate((temp_all, temperature))
            else:
                # User specified constant temperature.
                # Concatenate a matrix of size of internal data with repeated user values.
                user_arr = np.tile(each.sensors.temperature_deg_c.user.data,
                                   (np.size(each.sensors.temperature_deg_c.internal.data)))
                temp_all = np.concatenate((temp_all, user_arr))

        t_range = np.nanmax(temp_all) - np.nanmin(temp_all)
        ET.SubElement(temp_check, 'TemperatureChange', type='double', unitsCode='degC').text = '{:.2f}'.format(t_range)

        # (3) QRev_Message Node
        qa_check_keys = ['bt_vel', 'compass', 'depths', 'edges', 'extrapolation', 'gga_vel', 'movingbed', 'system_tst',
                         'temperature', 'transects', 'user', 'vtg_vel', 'w_vel']

        # For each qa check retrieve messages
        messages = []
        for key in qa_check_keys:
            qa_type = getattr(self.qa, key)
            if qa_type['messages']:
                for message in qa_type['messages']:
                    messages.append(message)

        # Sort messages with warning at top
        messages.sort(key=lambda x: x[1])

        if len(messages) > 0:
            temp = ''
            for message in messages:
                temp = temp + message[0]
            ET.SubElement(qa, 'QRev_Message', type='char').text = temp

        # (2) Instrument Node
        instrument = ET.SubElement(channel, 'Instrument')

        # (3) Manufacturer Node
        ET.SubElement(instrument, 'Manufacturer', type='char').text = self.transects[0].adcp.manufacturer

        # (3) Model Node
        ET.SubElement(instrument, 'Model', type='char').text = self.transects[0].adcp.model

        # (3) SerialNumber Node
        sn = self.transects[0].adcp.serial_num
        ET.SubElement(instrument, 'SerialNumber', type='char').text = str(sn)

        # (3) FirmwareVersion Node
        ver = self.transects[0].adcp.firmware
        ET.SubElement(instrument, 'FirmwareVersion', type='char').text = str(ver)

        # (3) Frequency Node
        freq = self.transects[0].adcp.frequency_khz
        if type(freq) == np.ndarray:
            freq = "Multi"
        ET.SubElement(instrument, 'Frequency', type='char', unitsCode='kHz').text = str(freq)

        # (3) BeamAngle Node
        ang = self.transects[0].adcp.beam_angle_deg
        ET.SubElement(instrument, 'BeamAngle', type='double', unitsCode='deg').text = '{:.1f}'.format(ang)

        # (3) BlankingDistance Node
        w_vel = []
        for each in self.transects:
            w_vel.append(each.w_vel)
        blank = []
        for each in w_vel:
            blank.append(each.blanking_distance_m)
        if isinstance(blank[0], float):
            temp = np.mean(blank)
            if self.transects[0].w_vel.excluded_dist_m > temp:
                temp = self.transects[0].w_vel.excluded_dist_m
        else:
            temp = self.transects[0].w_vel.excluded_dist_m
        ET.SubElement(instrument, 'BlankingDistance', type='double', unitsCode='m').text = '{:.4f}'.format(temp)

        # (3) InstrumentConfiguration Node
        commands = ''
        if self.transects[0].adcp.configuration_commands is not None:
            for each in self.transects[0].adcp.configuration_commands:
                if type(each) is str:
                    commands += each + '  '
            ET.SubElement(instrument, 'InstrumentConfiguration', type='char').text = commands

        # (2) Processing Node
        processing = ET.SubElement(channel, 'Processing')

        # (3) SoftwareVersion Node
        ET.SubElement(processing, 'SoftwareVersion', type='char').text = version

        # (3) Type Node
        ET.SubElement(processing, 'Type', type='char').text = self.processing

        # (3) AreaComputationMethod Node
        ET.SubElement(processing, 'AreaComputationMethod', type='char').text = 'Parallel'

        # (3) Navigation Node
        navigation = ET.SubElement(processing, 'Navigation')

        # (4) Reference Node
        ET.SubElement(navigation, 'Reference', type='char').text = self.transects[0].w_vel.nav_ref

        # (4) CompositeTrack
        ET.SubElement(navigation, 'CompositeTrack', type='char').text = self.transects[0].boat_vel.composite

        # (4) MagneticVariation Node
        mag_var = self.transects[0].sensors.heading_deg.internal.mag_var_deg
        ET.SubElement(navigation, 'MagneticVariation', type='double', unitsCode='deg').text = '{:.2f}'.format(mag_var)

        # (4) BeamFilter
        nav_data = getattr(self.transects[0].boat_vel, self.transects[0].boat_vel.selected)
        temp = nav_data.beam_filter
        if temp < 0:
            temp = 'Auto'
        else:
            temp = str(temp)
        ET.SubElement(navigation, 'BeamFilter', type='char').text = temp

        # (4) ErrorVelocityFilter Node
        evf = nav_data.d_filter
        if evf == 'Manual':
            evf = '{:.4f}'.format(nav_data.d_filter_threshold)
        ET.SubElement(navigation, 'ErrorVelocityFilter', type='char', unitsCode='mps').text = evf

        # (4) VerticalVelocityFilter Node
        vvf = nav_data.w_filter
        if vvf == 'Manual':
            vvf = '{:.4f}'.format(nav_data.w_filter_threshold)
        ET.SubElement(navigation, 'VerticalVelocityFilter', type='char', unitsCode='mps').text = vvf

        # (4) OtherFilter Node
        o_f = nav_data.smooth_filter
        ET.SubElement(navigation, 'OtherFilter', type='char').text = o_f

        # (4) GPSDifferentialQualityFilter Node
        temp = nav_data.gps_diff_qual_filter
        if temp:
            if isinstance(temp, int) or isinstance(temp, float):
                temp = str(temp)
            ET.SubElement(navigation, 'GPSDifferentialQualityFilter', type='char').text = temp

        # (4) GPSAltitudeFilter Node
        temp = nav_data.gps_altitude_filter
        if temp:
            if temp == 'Manual':
                temp = self.transects[0].boat_vel.gps_altitude_filter_change
            ET.SubElement(navigation, 'GPSAltitudeFilter', type='char', unitsCode='m').text = str(temp)

        # (4) HDOPChangeFilter
        temp = nav_data.gps_HDOP_filter
        if temp:
            if temp == 'Manual':
                temp = '{:.2f}'.format(self.transects[0].boat_vel.gps_hdop_filter_change)
            ET.SubElement(navigation, 'HDOPChangeFilter', type='char').text = temp

        # (4) HDOPThresholdFilter
        temp = nav_data.gps_HDOP_filter
        if temp:
            if temp == 'Manual':
                temp = '{:.2f}'.format(self.transects[0].boat_vel.gps_HDOP_filter_max)
            ET.SubElement(navigation, 'HDOPThresholdFilter', type='char').text = temp

        # (4) InterpolationType Node
        temp = nav_data.interpolate
        ET.SubElement(navigation, 'InterpolationType', type='char').text = temp

        # (3) Depth Node
        depth = ET.SubElement(processing, 'Depth')

        # (4) Reference Node
        if self.transects[0].depths.selected == 'bt_depths':
            temp = 'BT'
        elif self.transects[0].depths.selected == 'vb_depths':
            temp = 'VB'
        elif self.transects[0].depths.selected == 'ds_depths':
            temp = 'DS'
        ET.SubElement(depth, 'Reference', type='char').text = temp

        # (4) CompositeDepth Node
        ET.SubElement(depth, 'CompositeDepth', type='char').text = self.transects[0].depths.composite

        depth_data = getattr(self.transects[0].depths, self.transects[0].depths.selected)
        # (4) ADCPDepth Node
        temp = depth_data.draft_use_m
        ET.SubElement(depth, 'ADCPDepth', type='double', unitsCode='m').text = '{:.4f}'.format(temp)

        # (4) ADCPDepthConsistent Node
        drafts = []
        for transect in self.transects:
            if transect.checked:
                transect_depth = getattr(transect.depths, transect.depths.selected)
                drafts.append(transect_depth.draft_use_m)
        unique_drafts = set(drafts)
        num_drafts = len(unique_drafts)
        if num_drafts > 1:
            temp = 'No'
        else:
            temp = 'Yes'
        ET.SubElement(depth, 'ADCPDepthConsistent', type='boolean').text = temp

        # (4) FilterType Node
        temp = depth_data.filter_type
        ET.SubElement(depth, 'FilterType', type='char').text = temp

        # (4) InterpolationType Node
        temp = depth_data.interp_type
        ET.SubElement(depth, 'InterpolationType', type='char').text = temp

        # (4) AveragingMethod Node
        temp = depth_data.avg_method
        ET.SubElement(depth, 'AveragingMethod', type='char').text = temp

        # (4) ValidDataMethod Node
        temp = depth_data.valid_data_method
        ET.SubElement(depth, 'ValidDataMethod', type='char').text = temp

        # (3) WaterTrack Node
        water_track = ET.SubElement(processing, 'WaterTrack')

        # (4) ExcludedDistance Node
        temp = self.transects[0].w_vel.excluded_dist_m
        ET.SubElement(water_track, 'ExcludedDistance', type='double', unitsCode='m').text = '{:.4f}'.format(temp)

        # (4) BeamFilter Node
        temp = self.transects[0].w_vel.beam_filter
        if temp < 0:
            temp = 'Auto'
        else:
            temp = str(temp)
        ET.SubElement(water_track, 'BeamFilter', type='char').text = temp

        # (4) ErrorVelocityFilter Node
        temp = self.transects[0].w_vel.d_filter
        if temp == 'Manual':
            temp = '{:.4f}'.format(self.transects[0].w_vel.d_filter_threshold)
        ET.SubElement(water_track, 'ErrorVelocityFilter', type='char', unitsCode='mps').text = temp

        # (4) VerticalVelocityFilter Node
        temp = self.transects[0].w_vel.w_filter
        if temp == 'Manual':
            temp = '{:.4f}'.format(self.transects[0].w_vel.w_filter_threshold)
        ET.SubElement(water_track, 'VerticalVelocityFilter', type='char', unitsCode='mps').text = temp

        # (4) OtherFilter Node
        temp = self.transects[0].w_vel.smooth_filter
        ET.SubElement(water_track, 'OtherFilter', type='char').text = temp

        # (4) SNRFilter Node
        temp = self.transects[0].w_vel.snr_filter
        ET.SubElement(water_track, 'SNRFilter', type='char').text = temp

        # (4) CellInterpolation Node
        temp = self.transects[0].w_vel.interpolate_cells
        ET.SubElement(water_track, 'CellInterpolation', type='char').text = temp

        # (4) EnsembleInterpolation Node
        temp = self.transects[0].w_vel.interpolate_ens
        ET.SubElement(water_track, 'EnsembleInterpolation', type='char').text = temp

        # (3) Edge Node
        edge = ET.SubElement(processing, 'Edge')

        # (4) RectangularEdgeMethod Node
        temp = self.transects[0].edges.rec_edge_method
        ET.SubElement(edge, 'RectangularEdgeMethod', type='char').text = temp

        # (4) VelocityMethod Node
        temp = self.transects[0].edges.vel_method
        ET.SubElement(edge, 'VelocityMethod', type='char').text = temp

        # (4) LeftType Node
        typ = []
        for n in self.transects:
            if n.checked:
                typ.append(n.edges.left.type)
        unique_type = set(typ)
        num_types = len(unique_type)
        if num_types > 1:
            temp = 'Varies'
        else:
            temp = typ[0]
        ET.SubElement(edge, 'LeftType', type='char').text = temp

        # LeftEdgeCoefficient
        if temp == 'User Q':
            temp = 'N/A'
        elif temp == 'Varies':
            temp = 'N/A'
        else:
            coef = []
            for transect in self.transects:
                if transect.checked:
                    coef.append(QComp.edge_coef('left', transect))
            num_coef = len(set(coef))
            if num_coef > 1:
                temp = 'Varies'
            else:
                temp = '{:.4f}'.format(coef[0])
        ET.SubElement(edge, 'LeftEdgeCoefficient', type='char').text = temp

        # (4) RightType Node
        typ = []
        for n in self.transects:
            if n.checked:
                typ.append(n.edges.right.type)
        unique_type = set(typ)
        num_types = len(unique_type)
        if num_types > 1:
            temp = 'Varies'
        else:
            temp = typ[0]
        ET.SubElement(edge, 'RightType', type='char').text = temp

        # RightEdgeCoefficient
        if temp == 'User Q':
            temp = 'N/A'
        elif temp == 'Varies':
            temp = 'N/A'
        else:
            coef = []
            for transect in self.transects:
                if transect.checked:
                    coef.append(QComp.edge_coef('right', transect))
            num_coef = len(set(coef))
            if num_coef > 1:
                temp = 'Varies'
            else:
                temp = '{:.4f}'.format(coef[0])
        ET.SubElement(edge, 'RightEdgeCoefficient', type='char').text = temp

        # (3) Extrapolation Node
        extrap = ET.SubElement(processing, 'Extrapolation')

        # (4) TopMethod Node
        temp = self.transects[0].extrap.top_method
        ET.SubElement(extrap, 'TopMethod', type='char').text = temp

        # (4) BottomMethod Node
        temp = self.transects[0].extrap.bot_method
        ET.SubElement(extrap, 'BottomMethod', type='char').text = temp

        # (4) Exponent Node
        temp = self.transects[0].extrap.exponent
        ET.SubElement(extrap, 'Exponent', type='double').text = '{:.4f}'.format(temp)

        # (3) Sensor Node
        sensor = ET.SubElement(processing, 'Sensor')

        # (4) TemperatureSource Node
        temp = []
        for n in self.transects:
            if n.checked:
                # k+=1
                temp.append(n.sensors.temperature_deg_c.selected)
        sources = len(set(temp))
        if sources > 1:
            temp = 'Varies'
        else:
            temp = temp[0]
        ET.SubElement(sensor, 'TemperatureSource', type='char').text = temp

        # (4) Salinity
        temp = np.array([])
        for transect in self.transects:
            if transect.checked:
                sal_selected = getattr(transect.sensors.salinity_ppt, transect.sensors.salinity_ppt.selected)
                temp = np.append(temp, sal_selected.data)
        values = np.unique(temp)
        if len(values) > 1:
            temp = 'Varies'
        else:
            temp = '{:2.1f}'.format(values[0])
        ET.SubElement(sensor, 'Salinity', type='char', unitsCode='ppt').text = temp

        # (4) SpeedofSound Node
        temp = []
        for n in self.transects:
            if n.checked:
                temp.append(n.sensors.speed_of_sound_mps.selected)
        sources = len(set(temp))
        if sources > 1:
            temp = 'Varies'
        else:
            temp = temp[0]
        if temp == 'internal':
            temp = 'ADCP'
        ET.SubElement(sensor, 'SpeedofSound', type='char', unitsCode='mps').text = temp

        # (2) Transect Node
        other_prop = self.compute_measurement_properties(self)
        for n in range(len(self.transects)):
            if self.transects[n].checked:
                transect = ET.SubElement(channel, 'Transect')

                # (3) Filename Node
                temp = self.transects[n].file_name
                ET.SubElement(transect, 'Filename', type='char').text = temp

                # (3) StartDateTime Node
                temp = int(self.transects[n].date_time.start_serial_time)
                temp = datetime.datetime.fromtimestamp(temp).strftime('%m/%d/%Y %H:%M:%S')
                ET.SubElement(transect, 'StartDateTime', type='char').text = temp

                # (3) EndDateTime Node
                temp = int(self.transects[n].date_time.end_serial_time)
                temp = datetime.datetime.fromtimestamp(temp).strftime('%m/%d/%Y %H:%M:%S')
                ET.SubElement(transect, 'EndDateTime', type='char').text = temp

                # (3) Discharge Node
                t_q = ET.SubElement(transect, 'Discharge')

                # (4) Top Node
                temp = self.discharge[n].top
                ET.SubElement(t_q, 'Top', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

                # (4) Middle Node
                temp = self.discharge[n].middle
                ET.SubElement(t_q, 'Middle', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

                # (4) Bottom Node
                temp = self.discharge[n].bottom
                ET.SubElement(t_q, 'Bottom', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

                # (4) Left Node
                temp = self.discharge[n].left
                ET.SubElement(t_q, 'Left', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

                # (4) Right Node
                temp = self.discharge[n].right
                ET.SubElement(t_q, 'Right', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

                # (4) Total Node
                temp = self.discharge[n].total
                ET.SubElement(t_q, 'Total', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

                # (4) MovingBedPercentCorrection Node
                temp = ((self.discharge[n].total / self.discharge[n].total_uncorrected) - 1) * 100
                ET.SubElement(t_q, 'MovingBedPercentCorrection', type='double').text = '{:.2f}'.format(temp)

                # (3) Edge Node
                t_edge = ET.SubElement(transect, 'Edge')

                # (4) StartEdge Node
                temp = self.transects[n].start_edge
                ET.SubElement(t_edge, 'StartEdge', type='char').text = temp

                # (4) RectangularEdgeMethod Node
                temp = self.transects[n].edges.rec_edge_method
                ET.SubElement(t_edge, 'RectangularEdgeMethod', type='char').text = temp

                # (4) VelocityMethod Node
                temp = self.transects[n].edges.vel_method
                ET.SubElement(t_edge, 'VelocityMethod', type='char').text = temp

                # (4) LeftType Node
                temp = self.transects[n].edges.left.type
                ET.SubElement(t_edge, 'LeftType', type='char').text = temp

                # (4) LeftEdgeCoefficient Node
                temp = '{:.4f}'.format(QComp.edge_coef('left', self.transects[n]))
                ET.SubElement(t_edge, 'LeftEdgeCoefficient', type='double').text = temp

                # (4) LeftDistance Node
                temp = '{:.4f}'.format(self.transects[n].edges.left.distance_m)
                ET.SubElement(t_edge, 'LeftDistance', type='double', unitsCode='m').text = temp

                # (4) LeftNumberEnsembles
                temp = '{:.0f}'.format(self.transects[n].edges.left.number_ensembles)
                ET.SubElement(t_edge, 'LeftNumberEnsembles', type='double').text = temp

                # (4) RightType Node
                temp = self.transects[n].edges.right.type
                ET.SubElement(t_edge, 'RightType', type='char').text = temp

                # (4) RightEdgeCoefficient Node
                temp = '{:.4f}'.format(QComp.edge_coef('right', self.transects[n]))
                ET.SubElement(t_edge, 'RightEdgeCoefficient', type='double').text = temp

                # (4) RightDistance Node
                temp = '{:.4f}'.format(self.transects[n].edges.right.distance_m)
                ET.SubElement(t_edge, 'RightDistance', type='double', unitsCode='m').text = temp

                # (4) RightNumberEnsembles Node
                temp = '{:.0f}'.format(self.transects[n].edges.right.number_ensembles)
                ET.SubElement(t_edge, 'RightNumberEnsembles', type='double').text = temp

                # (3) Sensor Node
                t_sensor = ET.SubElement(transect, 'Sensor')

                # (4) TemperatureSource Node
                temp = self.transects[n].sensors.temperature_deg_c.selected
                ET.SubElement(t_sensor, 'TemperatureSource', type='char').text = temp

                # (4) MeanTemperature Node
                dat = getattr(self.transects[n].sensors.temperature_deg_c,
                              self.transects[n].sensors.temperature_deg_c.selected)
                temp = np.nanmean(dat.data)
                temp = '{:.2f}'.format(temp)
                ET.SubElement(t_sensor, 'MeanTemperature', type='double', unitsCode='degC').text = temp

                # (4) MeanSalinity
                sal_data = getattr(self.transects[n].sensors.salinity_ppt,
                                   self.transects[n].sensors.salinity_ppt.selected)
                temp = '{:.0f}'.format(np.nanmean(sal_data.data))
                ET.SubElement(t_sensor, 'MeanSalinity', type='double', unitsCode='ppt').text = temp

                # (4) SpeedofSoundSource Node
                sos_selected = getattr(self.transects[n].sensors.speed_of_sound_mps,
                                       self.transects[n].sensors.speed_of_sound_mps.selected)
                # if temp == 'internal':
                #     temp = 'ADCP'
                # elif temp == 'user':
                #     sos_data = getattr(self.transects[n].sensors.speed_of_sound_mps,
                #                        self.transects[n].sensors.speed_of_sound_mps.selected)
                #     if sos_data.source == 'Calculated':
                #         temp = 'Calc'
                #     else:
                #         temp = 'User'
                temp = sos_selected.source
                ET.SubElement(t_sensor, 'SpeedofSoundSource', type='char').text = temp

                # (4) SpeedofSound
                sos_data = getattr(self.transects[n].sensors.speed_of_sound_mps,
                                   self.transects[n].sensors.speed_of_sound_mps.selected)
                temp = '{:.4f}'.format(np.nanmean(sos_data.data))
                ET.SubElement(t_sensor, 'SpeedofSound', type='double', unitsCode='mps').text = temp

                # (3) Other Node
                t_other = ET.SubElement(transect, 'Other')

                # (4) Duration Node
                temp = '{:.2f}'.format(self.transects[n].date_time.transect_duration_sec)
                ET.SubElement(t_other, 'Duration', type='double', unitsCode='sec').text = temp

                # (4) Width
                temp = other_prop['width'][n]
                ET.SubElement(t_other, 'Width', type='double', unitsCode='m').text = '{:.4f}'.format(temp)

                # (4) Area
                temp = other_prop['area'][n]
                ET.SubElement(t_other, 'Area', type='double', unitsCode='sqm').text = '{:.4f}'.format(temp)

                # (4) MeanBoatSpeed
                temp = other_prop['avg_boat_speed'][n]
                ET.SubElement(t_other, 'MeanBoatSpeed', type='double', unitsCode='mps').text = '{:.4f}'.format(temp)

                # (4) QoverA
                temp = other_prop['avg_water_speed'][n]
                ET.SubElement(t_other, 'QoverA', type='double', unitsCode='mps').text = '{:.4f}'.format(temp)

                # (4) CourseMadeGood
                temp = other_prop['avg_boat_course'][n]
                ET.SubElement(t_other, 'CourseMadeGood', type='double', unitsCode='deg').text = '{:.2f}'.format(temp)

                # (4) MeanFlowDirection
                temp = other_prop['avg_water_dir'][n]
                ET.SubElement(t_other, 'MeanFlowDirection', type='double', unitsCode='deg').text = '{:.2f}'.format(temp)

                # (4) NumberofEnsembles
                temp = len(self.transects[n].boat_vel.bt_vel.u_processed_mps)
                ET.SubElement(t_other, 'NumberofEnsembles', type='integer').text = str(temp)

                # (4) PercentInvalidBins
                valid_ens, valid_cells = TransectData.raw_valid_data(self.transects[n])
                temp = (1 - (np.nansum(np.nansum(valid_cells))
                             / np.nansum(np.nansum(self.transects[n].w_vel.cells_above_sl)))) * 100
                ET.SubElement(t_other, 'PercentInvalidBins', type='double').text = '{:.2f}'.format(temp)

                # (4) PercentInvalidEnsembles
                temp = (1 - (np.nansum(valid_ens) / len(self.transects[n].boat_vel.bt_vel.u_processed_mps))) * 100
                ET.SubElement(t_other, 'PercentInvalidEns', type='double').text = '{:.2f}'.format(temp)

                pitch_source_selected = getattr(self.transects[n].sensors.pitch_deg,
                                                self.transects[n].sensors.pitch_deg.selected)
                roll_source_selected = getattr(self.transects[n].sensors.roll_deg,
                                               self.transects[n].sensors.roll_deg.selected)

                # (4) MeanPitch
                temp = np.nanmean(pitch_source_selected.data)
                ET.SubElement(t_other, 'MeanPitch', type='double', unitsCode='deg').text = '{:.2f}'.format(temp)

                # (4) MeanRoll
                temp = np.nanmean(roll_source_selected.data)
                ET.SubElement(t_other, 'MeanRoll', type='double', unitsCode='deg').text = '{:.2f}'.format(temp)

                # (4) PitchStdDev
                temp = np.nanstd(pitch_source_selected.data, ddof=1)
                ET.SubElement(t_other, 'PitchStdDev', type='double', unitsCode='deg').text = '{:.2f}'.format(temp)

                # (4) RollStdDev
                temp = np.nanstd(roll_source_selected.data, ddof=1)
                ET.SubElement(t_other, 'RollStdDev', type='double', unitsCode='deg').text = '{:.2f}'.format(temp)

                # (4) ADCPDepth
                depth_source_selected = getattr(self.transects[n].depths,
                                                self.transects[n].depths.selected)
                temp = depth_source_selected.draft_use_m
                ET.SubElement(t_other, 'ADCPDepth', type='double', unitsCode='m').text = '{:.4f}'.format(temp)

        # (2) ChannelSummary Node
        summary = ET.SubElement(channel, 'ChannelSummary')

        # (3) Discharge Node
        s_q = ET.SubElement(summary, 'Discharge')
        discharge = self.mean_discharges(self)

        # (4) Top
        temp = discharge['top_mean']
        ET.SubElement(s_q, 'Top', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

        # (4) Middle
        temp = discharge['mid_mean']
        ET.SubElement(s_q, 'Middle', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

        # (4) Bottom
        temp = discharge['bot_mean']
        ET.SubElement(s_q, 'Bottom', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

        # (4) Left
        temp = discharge['left_mean']
        ET.SubElement(s_q, 'Left', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

        # (4) Right
        temp = discharge['right_mean']
        ET.SubElement(s_q, 'Right', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

        # (4) Total
        temp = discharge['total_mean']
        ET.SubElement(s_q, 'Total', type='double', unitsCode='cms').text = '{:.3f}'.format(temp)

        # (4) MovingBedPercentCorrection
        temp = ((discharge['total_mean'] / discharge['uncorrected_mean']) - 1) * 100
        ET.SubElement(s_q, 'MovingBedPercentCorrection', type='double').text = '{:.2f}'.format(temp)

        # (3) Uncertainty Node
        s_u = ET.SubElement(summary, 'Uncertainty')
        uncertainty = self.uncertainty

        # (4) COV Node
        temp = uncertainty.cov
        ET.SubElement(s_u, 'COV', type='double').text = '{:.1f}'.format(temp)

        # (4) AutoRandom Node
        temp = uncertainty.cov_95
        ET.SubElement(s_u, 'AutoRandom', type='double').text = '{:.1f}'.format(temp)

        # (4) AutoInvalidData Node
        temp = uncertainty.invalid_95
        ET.SubElement(s_u, 'AutoInvalidData', type='double').text = '{:.1f}'.format(temp)

        # (4) AutoEdge Node
        temp = uncertainty.edges_95
        ET.SubElement(s_u, 'AutoEdges', type='double').text = '{:.1f}'.format(temp)

        # (4) AutoExtrapolation Node
        temp = uncertainty.extrapolation_95
        ET.SubElement(s_u, 'AutoExtrapolation', type='double').text = '{:.1f}'.format(temp)

        # (4) AutoMovingBed
        temp = uncertainty.moving_bed_95
        ET.SubElement(s_u, 'AutoMovingBed', type='double').text = '{:.1f}'.format(temp)

        # (4) AutoSystematic
        temp = uncertainty.systematic
        ET.SubElement(s_u, 'AutoSystematic', type='double').text = '{:.1f}'.format(temp)

        # (4) AutoTotal
        temp = uncertainty.total_95
        ET.SubElement(s_u, 'AutoTotal', type='double').text = '{:.1f}'.format(temp)

        # (4) UserRandom Node
        user_random = uncertainty.cov_95_user
        if user_random:
            ET.SubElement(s_u, 'UserRandom', type='double').text = '{:.1f}'.format(user_random)

        # (4) UserInvalidData Node
        user_invalid = uncertainty.invalid_95_user
        if user_invalid:
            ET.SubElement(s_u, 'UserInvalidData', type='double').text = '{:.1f}'.format(user_invalid)

        # (4) UserEdge
        user_edge = uncertainty.edges_95_user
        if user_edge:
            ET.SubElement(s_u, 'UserEdge', type='double').text = '{:.1f}'.format(user_edge)

        # (4) UserExtrapolation
        user_extrap = uncertainty.extrapolation_95_user
        if user_extrap:
            ET.SubElement(s_u, 'UserExtrapolation', type='double').text = '{:.1f}'.format(user_extrap)

        # (4) UserMovingBed
        user_mb = uncertainty.moving_bed_95_user
        if user_mb:
            ET.SubElement(s_u, 'UserMovingBed', type='double').text = '{:.1f}'.format(user_mb)

        # (4) UserSystematic
        user_systematic = uncertainty.systematic_user
        if user_systematic:
            ET.SubElement(s_u, 'UserSystematic', type='double').text = '{:.1f}'.format(user_systematic)

        # (4) UserTotal Node
        temp = uncertainty.total_95_user
        ET.SubElement(s_u, 'UserTotal', type='double').text = '{:.1f}'.format(temp)

        # (4) Random
        if user_random:
            temp = user_random
        else:
            temp = uncertainty.cov_95
        ET.SubElement(s_u, 'Random', type='double').text = '{:.1f}'.format(temp)

        # (4) InvalidData
        if user_invalid:
            temp = user_invalid
        else:
            temp = uncertainty.invalid_95
        ET.SubElement(s_u, 'InvalidData', type='double').text = '{:.1f}'.format(temp)

        # (4) Edge
        if user_edge:
            temp = user_edge
        else:
            temp = uncertainty.edges_95
        ET.SubElement(s_u, 'Edge', type='double').text = '{:.1f}'.format(temp)

        # (4) Extrapolation
        if user_extrap:
            temp = user_extrap
        else:
            temp = uncertainty.extrapolation_95
        ET.SubElement(s_u, 'Extrapolation', type='double').text = '{:.1f}'.format(temp)

        # (4) MovingBed
        if user_mb:
            temp = user_mb
        else:
            temp = uncertainty.moving_bed_95
        ET.SubElement(s_u, 'MovingBed', type='double').text = '{:.1f}'.format(temp)

        # (4) Systematic
        if user_systematic:
            temp = user_systematic
        else:
            temp = uncertainty.systematic
        ET.SubElement(s_u, 'Systematic', type='double').text = '{:.1f}'.format(temp)

        # (4) UserTotal Node
        temp = uncertainty.total_95_user
        ET.SubElement(s_u, 'Total', type='double').text = '{:.1f}'.format(temp)

        # (3) Other Node
        s_o = ET.SubElement(summary, 'Other')

        # (4) MeanWidth
        temp = other_prop['width'][-1]
        ET.SubElement(s_o, 'MeanWidth', type='double', unitsCode='m').text = '{:.4f}'.format(temp)

        # (4) WidthCOV
        temp = other_prop['width_cov'][-1]
        ET.SubElement(s_o, 'WidthCOV', type='double').text = '{:.4f}'.format(temp)

        # (4) MeanArea
        temp = other_prop['area'][-1]
        ET.SubElement(s_o, 'MeanArea', type='double', unitsCode='sqm').text = '{:.4f}'.format(temp)

        # (4) AreaCOV
        temp = other_prop['area_cov'][-1]
        ET.SubElement(s_o, 'AreaCOV', type='double').text = '{:.2f}'.format(temp)

        # (4) MeanBoatSpeed
        temp = other_prop['avg_boat_speed'][-1]
        ET.SubElement(s_o, 'MeanBoatSpeed', type='double', unitsCode='mps').text = '{:.4f}'.format(temp)

        # (4) MeanQoverA
        temp = other_prop['avg_water_speed'][-1]
        ET.SubElement(s_o, 'MeanQoverA', type='double', unitsCode='mps').text = '{:.4f}'.format(temp)

        # (4) MeanCourseMadeGood
        temp = other_prop['avg_boat_course'][-1]
        ET.SubElement(s_o, 'MeanCourseMadeGood', type='double', unitsCode='deg').text = '{:.2f}'.format(temp)

        # (4) MeanFlowDirection
        temp = other_prop['avg_water_dir'][-1]
        ET.SubElement(s_o, 'MeanFlowDirection', type='double', unitsCode='deg').text = '{:.2f}'.format(temp)

        # (4) MeanDepth
        temp = other_prop['avg_depth'][-1]
        ET.SubElement(s_o, 'MeanDepth', type='double', unitsCode='m').text = '{:.4f}'.format(temp)

        # (4) MaximumDepth
        temp = other_prop['max_depth'][-1]
        ET.SubElement(s_o, 'MaximumDepth', type='double', unitsCode='m').text = '{:.4f}'.format(temp)

        # (4) MaximumWaterSpeed
        temp = other_prop['max_water_speed'][-1]
        ET.SubElement(s_o, 'MaximumWaterSpeed', type='double', unitsCode='mps').text = '{:.4f}'.format(temp)

        # (4) NumberofTransects
        temp = len(self.checked_transects(self))
        ET.SubElement(s_o, 'NumberofTransects', type='integer').text = str(temp)

        # (4) Duration
        temp = self.measurement_duration(self)
        ET.SubElement(s_o, 'Duration', type='double', unitsCode='sec').text = '{:.2f}'.format(temp)

        # (4) LeftQPer
        temp = 100 * discharge['left_mean'] / discharge['total_mean']
        ET.SubElement(s_o, 'LeftQPer', type='double').text = '{:.2f}'.format(temp)

        # (4) RightQPer
        temp = 100 * discharge['right_mean'] / discharge['total_mean']
        ET.SubElement(s_o, 'RightQPer', type='double').text = '{:.2f}'.format(temp)

        # (4) InvalidCellsQPer
        temp = 100 * discharge['int_cells_mean'] / discharge['total_mean']
        ET.SubElement(s_o, 'InvalidCellsQPer', type='double').text = '{:.2f}'.format(temp)

        # (4) InvalidEnsQPer
        temp = 100 * discharge['int_ensembles_mean'] / discharge['total_mean']
        ET.SubElement(s_o, 'InvalidEnsQPer', type='double').text = '{:.2f}'.format(temp)

        # (4) UserRating
        if self.user_rating:
            temp = self.user_rating
        else:
            temp = 'Not Rated'
        ET.SubElement(s_o, 'UserRating', type='char').text = temp

        # (4) DischargePPDefault
        temp = self.extrap_fit.q_sensitivity.q_pp_mean
        ET.SubElement(s_o, 'DischargePPDefault', type='double').text = '{:.2f}'.format(temp)

        # (4) UserComment
        if len(self.comments) > 1:
            temp = ''
            for comment in self.comments:
                temp = temp + comment.replace('\n', ' |||') + ' |||'
            ET.SubElement(s_o, 'UserComment', type='char').text = temp

        # Create xml output file
        with open(file_name, 'wb') as xml_file:
            # Create binary coded output file
            et = ET.ElementTree(channel)
            root = et.getroot()
            xml_out = ET.tostring(root)
            # Add stylesheet instructions
            xml_out = b'<?xml-stylesheet type= "text/xsl" href="QRevStylesheet.xsl"?>' + xml_out
            # Add tabs to make output more readable and apply utf-8 encoding
            xml_out = parseString(xml_out).toprettyxml(encoding='utf-8')
            # Write file
            xml_file.write(xml_out)

if __name__ == '__main__':
    pass
