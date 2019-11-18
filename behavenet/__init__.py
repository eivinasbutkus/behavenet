import os
import json


def get_params_dir():
    from pathlib import Path
    return Path.home()


def setup():
    """Set up the user's data, results, and figure directories; store info in user's home dir"""

    dirs = {}

    prompt = "Enter base data directory: "
    dirs['data_dir'] = input(prompt)

    prompt = "Enter base results directory: "
    dirs['save_dir'] = input(prompt)

    prompt = "Enter base figures directory: "
    dirs['fig_dir'] = input(prompt)

    params_dir = os.path.join(get_params_dir(), '.behavenet')
    if not os.path.exists(params_dir):
        os.makedirs(params_dir)
    params_file = os.path.join(params_dir, 'directories')
    with open(params_file, 'w') as f:
        json.dump(dirs, f, sort_keys=False, indent=4)

    print('Directories are now stored in %s' % params_file)


def add_dataset():
    """Set up information about a new dataset"""

    params_dataset = {
        'lab': ['name of experimenter/lab (str): ', str],
        'expt': ['name of experiment (str): ', str],
        'animal': ['example animal name (str): ', str],
        'session': ['example session name (str): ', str],
        'trial_splits': ['trial splits as train;val;test;gap (e.g. 8;1;1;0): ', str],
    }
    params_video = {
        'x_pixels': ['number of x pixels (int): ', int],
        'y_pixels': ['number of y pixels (int): ', int],
        'n_input_channels': ['number of camera views (int): ', int],
        'use_output_mask': ['are you applying any masks to the video? (True/False): ', bool],
        'frame_rate': ['frame rate of video (Hz) (float): ', float],
    }
    params_neural = {
        'neural_type': ['neural data type (str - spikes or ca): ', str],
    }

    params = {}
    print('Please enter the following information about the dataset:')
    for key, val in params_dataset.items():
        if val[1] == bool:
            params[key] = True if input(val[0]).lower() == 'true' else False
        else:
            params[key] = val[1](input(val[0]))

    print('Please enter the following information about the behavioral video:')
    for key, val in params_video.items():
        if val[1] == bool:
            params[key] = True if input(val[0]).lower() == 'true' else False
        else:
            params[key] = val[1](input(val[0]))

    print('Please enter the following information about the neural data:')
    for key, val in params_neural.items():
        if val[1] == bool:
            params[key] = True if input(val[0]).lower() == 'true' else False
        else:
            params[key] = val[1](input(val[0]))
    params['neural_bin_size'] = 1.0 / float(params['frame_rate'])
    params['approx_batch_size'] = 200

    params_dir = os.path.join(get_params_dir(), '.behavenet')
    if not os.path.exists(params_dir):
        os.makedirs(params_dir)
    params_file = os.path.join(params_dir, str('%s_%s_params' % (params['lab'], params['expt'])))
    with open(params_file, 'w') as f:
        json.dump(params, f, sort_keys=False, indent=4)

    print('Dataset params are now stored in %s' % params_file)
