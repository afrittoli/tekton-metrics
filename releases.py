#!/usr/bin/env python3

import hashlib
import json
import matplotlib.pyplot as plt
import os
import os.path
import re
import requests
from requests.auth import HTTPBasicAuth
import sys

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_API_REPOS = "/orgs/{org}/repos"
GITHUB_API_RELEASES = "/repos/{owner}/{repo}/releases?per_page=100"
GITHUB_ORG = "tektoncd"
GITHUB_CACHE = '.cache'

REGEX_MAJOR = re.compile('^v[0-9]+\.[0-9]+\.0($|-1$)')
REGEX_RC = re.compile('^v[0-9]+\.[0-9]+\.0-rc[0-9]+$')

def github_request(url):
    if cache := github_from_cache(url):
      return cache
    username = os.getenv("GITHUB_USERNAME")
    token = os.getenv("GITHUB_TOKEN")
    params = {}
    if username and token:
        params['auth'] = HTTPBasicAuth(username, token)
    r = requests.get(url, **params)
    try:
      r.raise_for_status()
    except requests.exceptions.HTTPError as e:
      print("Error calling api {}: {}".format(url, e))
      sys.exit(1)
    github_to_cache(url, r.json())
    return r.json()


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


def main():
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


if __name__ == "__main__":
    # execute only if run as a script
    main()