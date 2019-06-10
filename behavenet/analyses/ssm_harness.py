"""
Use SSM to learn an autoregressive HMM that takes neural activity
as an input and outputs a distribution over continuous trajectories.
Here, the trajectories are the latent codes of a VAE.
"""
import os
import time
import copy
import pickle
from tqdm.auto import trange

import numpy as np
import numpy.random as npr
from scipy.stats import multivariate_normal as mvn
from scipy.special import logsumexp
from scipy.ndimage import gaussian_filter1d

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.animation as manimation
from matplotlib.animation import FFMpegWriter
from matplotlib.patches import Rectangle
from matplotlib.cm import jet
from matplotlib.gridspec import GridSpec

import seaborn as sns

from sklearn.cluster import KMeans
from sklearn.mixture import GaussianMixture
from sklearn.decomposition import PCA

from ssm import HMM
from ssm.primitives import hmm_sample, lds_sample
from ssm.observations import GaussianObservations
from ssm.init_state_distns import InitialStateDistribution
from ssm.preprocessing import trend_filter, pca_with_imputation


npr.seed(0)
sns.set_style("white")
sns.set_context("talk")


# Helper function to cache results
def cached(output_dir, results_name):
    def _cache(func):
        def func_wrapper(*args, **kwargs):
            results_file = os.path.join(output_dir, results_name)
            if not results_file.endswith(".pkl"):
                results_file += ".pkl"

            if os.path.exists(results_file):
                with open(results_file, "rb") as f:
                    results = pickle.load(f)
            else:
                results = func(*args, **kwargs)
                with open(results_file, "wb") as f:
                    pickle.dump(results, f)

            return results
        return func_wrapper
    return _cache


# Estimate firing rates and then run PCA
def preprocess_neural_data(neural_data, fs=40, window=0.1):
    # neural data should be trials x time x neurons (if no trials, just have 0th dimension of 1)
    [n_trials, trial_length, n_neurons] = neural_data.shape

    filter_size = int(window * fs)
    rates = gaussian_filter1d(neural_data, filter_size, axis=1) * fs
    max_rates = np.max(np.max(rates, axis=0),axis=0) # get max over trials and time for each neuron
    normalized_rates = rates / max_rates

    pca = PCA(20)
    pca.fit(normalized_rates.reshape((n_trials*trial_length,n_neurons)))
    lowd_neural_data = pca.transform(normalized_rates.reshape((n_trials*trial_length,n_neurons)))

    return normalized_rates, lowd_neural_data.reshape((n_trials,trial_length,20)), pca, max_rates


# Fit an HMM
def fit_model(num_discrete_states,
              data_dimension,
              input_dimension,
              model_kwargs,
              training_data,
              validation_data,
              test_data,
              training_inputs=None,
              validation_inputs=None,
              test_inputs=None
              ):

    model = HMM(K=num_discrete_states, D=data_dimension, M=input_dimension, **model_kwargs)
    model.initialize(training_data, inputs=training_inputs)
    model.observations.initialize(training_data, training_inputs, localize=False)

    # Run EM. Specify tolerances for overall convergence and each M-step's convergence
    lps = model.fit(training_data,
                    inputs=training_inputs,
                    method="em", num_em_iters=50, tolerance=1e-1,
                    transitions_mstep_kwargs=dict(optimizer="lbfgs", tol=1e-3))

    # Compute stats
    val_ll = model.log_likelihood(validation_data, inputs=validation_inputs)
    test_ll = model.log_likelihood(test_data, inputs=test_inputs)

    # Sort states by usage
    training_inputs = [None] * len(training_data) if training_inputs is None else training_inputs
    train_states = [model.most_likely_states(x, u) for x, u in zip(training_data, training_inputs)]
    usage = np.bincount(np.concatenate(train_states), minlength=num_discrete_states)
    model.permute(np.argsort(-usage))
    train_states = [model.most_likely_states(x, input=u) for x, u in zip(training_data, training_inputs)]

    # Combine results
    return dict(model=model,
                train_states=train_states,
                lps=lps,
                val_ll=val_ll,
                test_ll=test_ll)


# Sample from the arhmm
def sample_arhmm(arhmm_orig, test_input, num_samples=100, with_noise=True):
    arhmm = copy.deepcopy(arhmm_orig)
    # Reduce noise, increase stickiness
    # arhmm.observations._sqrt_Sigmas = np.linalg.cholesky(arhmm.observations.Sigmas * .1)
    # arhmm.observations._log_nus += 2
    # arhmm.transitions.log_Ps += 1 * np.eye(arhmm.K)

    T = test_input.shape[0]
    z_smpls, x_smpls = [], []
    for smpl in trange(num_samples):
        z_smpl, x_smpl = arhmm.sample(T=T, input=test_input, with_noise=with_noise)
        z_smpls.append(z_smpl)
        x_smpls.append(x_smpl)

    x_smpl_mean = np.mean(x_smpls, axis=0)
    x_smpl_std = np.std(x_smpls, axis=0)

    return z_smpls, x_smpls


def plot_neural_activity(lowd_neural_data, normalized_rates, slc=(0, 10000)):
    D = lowd_neural_data.shape[1]

    fig = plt.figure(figsize=(8, 7))
    plt.subplot(211)
    plt.plot(lowd_neural_data[slice(*slc)] - 3 * np.arange(D))
    plt.xlim(plt.xlim(0, slc[1] - slc[0]))
    plt.xticks([])
    plt.ylabel("PC")

    plt.subplot(212)
    plt.imshow(normalized_rates[slice(*slc)].T, aspect="auto", cmap="Greys", vmax=1)
    plt.xlim(0, slc[1] - slc[0])
    plt.xlabel("time")
    plt.ylabel("neuron")

    return fig


def plot_validation_likelihoods(all_results, line_styles={}, T_val=1):
    # Plot the log likelihood of the validation data
    fig = plt.figure(figsize=(8, 6))
    for model_name, model_results in all_results.items():
        Ks = sorted(model_results.keys())
        val_lls = np.array([model_results[K]['val_ll'] for K in Ks])
        plt.plot(Ks, val_lls / T_val,
                 ls=line_styles[model_name], marker='o', alpha=1,
                 label=model_name)
    plt.legend(loc="lower right")
    plt.xlabel("num. discrete states")
    plt.ylabel("validation log lkhd.")
    return fig


def plot_sampled_latents(training_data, x_smpls, lw=0.5):
    T, D = training_data.shape

    # Compute mean and std of sampels
    x_smpl_mean = np.mean(x_smpls, axis=0)
    x_smpl_std = np.std(x_smpls, axis=0)
    spc = 1.1 * abs(training_data.max())


    fig = plt.figure(figsize=(8, 6))
    for d in range(D):
        h = plt.plot(x_smpls[0][:, d] - spc * d, lw=lw)[0]

        if len(x_smpls) < 10:
            i_plot = np.arange(len(x_smpls))
        else:
            i_plot = np.random.choice(len(x_smpls), size=10, replace=False)

        for i in i_plot:
            plt.plot(x_smpls[i][:, d] - spc * d, color=h.get_color(), lw=lw)

        # Plot standard deviation of samples
        plt.fill_between(np.arange(T),
                         x_smpl_mean[:, d] - 2 * x_smpl_std[:, d] - spc * d,
                         x_smpl_mean[:, d] + 2 * x_smpl_std[:, d] - spc * d,
                         color=h.get_color(), alpha=0.25)

        # Plot sample mean
        # plt.plot(x_smpl_mean[:, d] - 5 * d, color=h.get_color(), lw=2)

        plt.plot(training_data[:, d] - spc * d, '-k', alpha=0.5)
                 # label="data" if d==0 else None)

    plt.legend(loc="lower right")

    plt.xlabel("time")
    plt.yticks(-np.arange(D) * spc, ["dim {}".format(i+1) for i in range(D)])
    plt.ylabel("continuous latent state")
    # plt.savefig("decoded_latents.png")
    return fig


def plot_neural_and_discrete_samples(training_input, z_smpls, z_inf):
    T, M = training_input.shape

    fig = plt.figure(figsize=(8, 8))

    gs = GridSpec(3, 1, height_ratios=(1,1.5,.2))

    plt.subplot(gs[0,0])
    plt.plot(training_input - 5 * np.arange(M), '-k')
    plt.xlim(0, T)
    plt.xticks([])
    plt.yticks(-5 * np.arange(M), ["PC{}".format(m+1) for m in range(M)])
    plt.ylabel("PCs of neural activity")

    plt.subplot(gs[1,0])
    plt.imshow(np.array(z_smpls), aspect="auto", cmap="jet")
    plt.xticks([])
    plt.ylabel("Decoded State Smpls")

    plt.subplot(gs[2,0])
    plt.imshow(z_inf[None,:], aspect="auto", cmap="jet")
    plt.xlabel("time bin")
    plt.ylabel("Inf. State")
    plt.yticks([])

    # plt.savefig("decoded_zs.png")
    return fig


def make_hollywood_movie(K, real_image_stack, z_inf, decoded_image_stacks, z_smpls, titles=None,
                         filename="hollywood.mp4", name="real and decoded movies", same_vlim=True):
    """
    Make "Hollywood Squares" movie of real and comparison data
    """
    FFMpegWriter = manimation.writers['ffmpeg']
    metadata = dict(title="decoded_movie")
    writer = FFMpegWriter(fps=30, bitrate=-1, metadata=metadata)


    N_samples = len(decoded_image_stacks)
    assert N_samples < 10
    width = 1 / (N_samples + 1)

    if same_vlim:
        vmin = real_image_stack.min() * np.ones(N_samples + 1)
        vmax = real_image_stack.max() * np.ones(N_samples + 1)
    else:
        vmin = [real_image_stack.min()] + [np.percentile(stack, 0) for stack in decoded_image_stacks]
        vmax = [real_image_stack.max()] + [np.percentile(stack, 99.99) for stack in decoded_image_stacks]

    if titles is None:
        titles = ["Real"] + ["Decoded {}".format(j + 1) for j in range(N_samples)]
    else:
        assert len(titles) == N_samples + 1

    fig_dim_div = decoded_image_stacks[0].shape[2]*(N_samples+1)/10

    fig = plt.figure(figsize=(decoded_image_stacks[0].shape[2]*(N_samples+1)/fig_dim_div, decoded_image_stacks[0].shape[1]/fig_dim_div))
    #fig = plt.figure(figsize=(3 * (N_samples + 1), 3))
    ax1 = plt.axes((0, 0, width, 1))
    im1 = ax1.imshow(real_image_stack[0, :, :, 0], vmin=vmin[0], vmax=vmax[0], cmap="Greys_r")
    r1 = Rectangle((.9, .9), .05, .05, color=jet(z_inf[0] / (K - 1)))
    ax1.add_patch(r1)
    ax1.set_xticks([])
    ax1.set_yticks([])
    ti = ax1.set_title(titles[0], pad=-15)
    ti.set_color("white")

    axs, ims, rs = [], [], []
    for j in range(N_samples):
        ax = plt.axes(((j+1) * width, 0, width, 1))
        im = ax.imshow(decoded_image_stacks[j][0, :, :, 0], vmin=vmin[j+1], vmax=vmax[j+1], cmap="Greys_r")
        r = Rectangle((.9, .9), .05, .05, color=jet(z_smpls[j][0] / (K - 1)))
        ax.add_patch(r)
        ax.set_xticks([])
        ax.set_yticks([])
        ti = ax.set_title(titles[j+1], pad=-15)
        ti.set_color("white")

        axs.append(ax)
        ims.append(im)
        rs.append(r)


    def update_frame(i):
        # Update the images
        im1.set_data(real_image_stack[i, :, :, 0])
        r1.set_color(jet(z_inf[i] / (K - 1)))

        for j in range(N_samples):
            ims[j].set_data(decoded_image_stacks[j][i, :, :, 0])
            rs[j].set_color(jet(z_smpls[j][i] / (K - 1)))
        #title.set_text("Frame {}".format(i))

    with writer.saving(fig, filename, 100):
        for i in trange(1, real_image_stack.shape[0]):
            update_frame(i)
            writer.grab_frame()

