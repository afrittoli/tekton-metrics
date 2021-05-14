#!/usr/bin/env python3

import argparse
import git
import hashlib
import json
import matplotlib.pyplot as plt
import numpy as np
import os
import os.path
import pandas as pd
import re
import requests
from requests.auth import HTTPBasicAuth
import sys

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_API_REPOS = "/orgs/{org}/repos"
GITHUB_API_RELEASES = "/repos/{owner}/{repo}/releases?per_page=100"
GITHUB_API_PRS = "/repos/{owner}/{repo}/pulls?per_page=100&state={state}"
GITHUB_ORG = "tektoncd"
GITHUB_CACHE = '.cache'
GIT_CLONE_FOLDER = os.path.join(GITHUB_CACHE, 'git')

METRICS = ['release_plot', 'lead_time_prs']

REGEX_MAJOR = re.compile('^v[0-9]+\.[0-9]+\.0($|-1$)')
REGEX_RC = re.compile('^v[0-9]+\.[0-9]+\.0-rc[0-9]+$')


def clone_repo(org, repo, update=False):
    project = (org, repo)
    repo = "/".join(project)
    clone_dir = os.path.join(GIT_CLONE_FOLDER, *project)

    if os.path.isdir(clone_dir):
        if not update:
            # print(f'{project}: Cache folder {clone_dir} found, skipping clone.')
            return repo, git.Repo(clone_dir)
        # Cleanup and update via fetch --all
        print(f'{project}: updating started')
        cloned_repo = git.Repo(clone_dir)
        cloned_repo.git.reset('--hard')
        cloned_repo.git.clean('-xdf')
        cloned_repo.git.fetch('--all')
        print(f'{project}: updating completed')
        return repo, cloned_repo

    # Clone the repo
    print(f'{project}: cloning started')
    cloned_repo = git.Repo.clone_from('https://github.com/' + repo, clone_dir)
    print(f'{project}: cloning completed')
    return repo, cloned_repo


def _github_single_request(url, params):
    r = requests.get(url, **params)
    try:
      r.raise_for_status()
    except requests.exceptions.HTTPError as e:
      print("Error calling api {}: {}".format(url, e))
      sys.exit(1)
    return r


def github_request(url):
    if cache := github_from_cache(url):
      return cache
    username = os.getenv("GITHUB_USERNAME")
    token = os.getenv("GITHUB_TOKEN")
    params = {}
    if username and token:
        params['auth'] = HTTPBasicAuth(username, token)
    r = _github_single_request(url, params)
    result = r.json()
    # Loop through all pages
    while 'next' in r.links.keys():
      r = _github_single_request(r.links['next']['url'], params)
      result.extend(r.json())
    github_to_cache(url, result)
    return result


def github_from_cache(url):
    cache_file = os.path.sep.join(
      [GITHUB_CACHE, hashlib.sha256(url.encode('utf-8')).hexdigest()])
    if os.path.isfile(cache_file):
      with open(cache_file) as from_cache:
        return json.load(from_cache)
    return ""


def github_to_cache(url, jsondata):
    os.makedirs(GITHUB_CACHE, exist_ok=True)
    cache_file = os.path.sep.join(
      [GITHUB_CACHE, hashlib.sha256(url.encode('utf-8')).hexdigest()])
    with open(cache_file, 'w') as cache_output:
      json.dump(jsondata, cache_output)


def get_repos():
    url = GITHUB_API_ROOT + GITHUB_API_REPOS.format(org=GITHUB_ORG)
    return github_request(url)


def get_releases(repo):
    url = GITHUB_API_ROOT + GITHUB_API_RELEASES.format(owner=GITHUB_ORG, repo=repo)
    return github_request(url)


def get_prs(repo, state):
    url = GITHUB_API_ROOT + GITHUB_API_PRS.format(
      owner=GITHUB_ORG, repo=repo, state=state)
    return github_request(url)


def color_from_release(name):
    if REGEX_MAJOR.match(name):
      return 800
    elif REGEX_RC.match(name):
      return 400
    return 64


def plot_releases(repos, releases):
    # data is a list of tuples, sorted by date:
    # (date, project, version)
    X = [rd[0] for rd in releases]
    Y = [rd[1] for rd in releases]
    N = [rd[2] if REGEX_MAJOR.match(rd[2]) else '' for rd in releases]
    repos_numbers = {repo:repos.index(repo) for repo in repos}
    colors = [repos_numbers[y] for y in Y]
    size = [color_from_release(x[2]) for x in releases]
    fig = plt.figure(figsize=(16, 9))
    ax = plt.subplot()
    # ax.grid(which='major', axis='x', linestyle='--')
    # ax.set_aspect(aspect=15)
    plt.scatter(X, Y, c=colors, cmap="Dark2", s=size, alpha=0.5)
    for i, version in enumerate(N):
      short_version = ".".join(version.split(".")[:-1])
      if Y[i] == 'pipeline' and short_version in ['v0.1', 'v0.2', 'v0.3']:
        continue
      if Y[i] == 'operator' and short_version in ['v0.19']:
        continue
      ax.annotate(short_version, xy=(X[i], Y[i]), fontsize=8,
        alpha=0.9, xytext=(-10, 17), textcoords="offset points")
    plt.xticks(rotation=45)
    every_nth = 3
    for n, label in enumerate(ax.xaxis.get_ticklabels()):
        if n % every_nth != 0:
            label.set_visible(False)
        else:
            label.set_fontsize(7)
    plt.title('Releases over time')
    fig.tight_layout()
    fig.savefig("releases.png", dpi=300)


def release_plot():
    repos = [x['name'] for x in get_repos()]
    data = []
    for repo in repos:
        releases = get_releases(repo)
        for release in releases:
            # Take the date only
            release_date = release['published_at'].split('T')[0]
            data.append((release_date, repo, release['tag_name']))
    release_dates = sorted(data, key=lambda x: x[0])
    plot_releases(repos, release_dates)


def belongs_to(commit_sha, repo):
    # Return the closes release commit belongs to if any
    try:
      reference = repo.git.describe(commit_sha, '--contains')
    except git.exc.GitCommandError as gce:
      # Ignore if describe fails because there is no tag
      if 'fatal: cannot describe' in str(gce):
        return
      else:
        raise gce
    tag = re.search('^([^~]*)(?:$|~[0-9]+$)', reference)
    return tag.group(1) if tag else None


def lead_time_prs():
    repos = [x['name'] for x in get_repos()]
    prs_all = {}
    for repo in repos:
        print(f"Processing {repo}")
        # Use a local clone so we can "git describe"
        _, clone = clone_repo(GITHUB_ORG, repo)
        # Use the list of releases from the GitHub API
        # to only use tags that are published to releases
        releases_raw = get_releases(repo)
        # If there are no releases, just skip this repo
        if not releases_raw:
          continue
        releases_published = {r['tag_name']:r['published_at'] for r in releases_raw}
        # List all closed PRs
        prs = get_prs(repo, "closed")
        prs_data_list = []
        for pr in prs:
            # Filter out PRs that were not merged
            if not pr['merged_at']:
                continue
            release = belongs_to(pr['merge_commit_sha'], clone)
            # Filter out PRs that have no tag on top or whose tags
            # do not match a published release.
            # The assumption here is that the only tags we have in repos
            # with releases are those associated to releases.
            if not release or release not in releases_published:
                continue
            prs_data_list.append(
              dict(number=pr['number'],
                   release=release,
                   created_at=pd.Timestamp(pr['created_at']),
                   merged_at=pd.Timestamp(pr['merged_at']),
                   released_at=pd.Timestamp(releases_published[release])))
        prs_data = pd.DataFrame(prs_data_list)
        # There might be no data at all for a repo
        if not prs_data.empty:
            stats = pd.DataFrame()
            stats['open_to_release_days'] = \
              (prs_data['released_at'] - prs_data['created_at']).astype('timedelta64[D]')
            stats['merged_to_release_days'] = \
              (prs_data['released_at'] - prs_data['merged_at']).astype('timedelta64[D]')
            prs_all[repo] = stats.apply(np.average, axis=0)
            # Add the release column for grouping
            stats['release'] = prs_data['release']
            # grouped = stats.groupby('release').mean().plot()
            plot = stats.groupby('release').mean().plot()
            fig = plot.get_figure()
            fig.savefig(f"lead_time_{repo}.png")
            # grouped.apply(lambda x: np.mean(x)).plot()
    for repo, stats in prs_all.items():
      print("{}:\n{}\n".format(repo, stats.to_string()))


if __name__ == "__main__":
    # execute only if run as a script
    parser = argparse.ArgumentParser(description='Tekton metrics fun')
    parser.add_argument('--metric', default='lead_time_prs',
                        help='name of the metric to generate, one of {}'.format(METRICS),
                        choices=METRICS)
    args = parser.parse_args()
    locals()[args.metric]()