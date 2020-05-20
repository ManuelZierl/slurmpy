r"""
# send in job name and kwargs for slurm params:
>>> s = Slurm("job-name", {"account": "ucgd-kp", "partition": "ucgd-kp"})
>>> print(str(s))
#!/bin/bash
<BLANKLINE>
#SBATCH -e logs/job-name.%J.err
#SBATCH -o logs/job-name.%J.out
#SBATCH -J job-name
<BLANKLINE>
#SBATCH --account=ucgd-kp
#SBATCH --partition=ucgd-kp
#SBATCH --time=84:00:00
<BLANKLINE>
set -eo pipefail -o nounset
<BLANKLINE>
__script__

>>> s = Slurm("job-name", {"account": "ucgd-kp", "partition": "ucgd-kp"}, bash_strict=False)
>>> print(str(s))
#!/bin/bash
<BLANKLINE>
#SBATCH -e logs/job-name.%J.err
#SBATCH -o logs/job-name.%J.out
#SBATCH -J job-name
<BLANKLINE>
#SBATCH --account=ucgd-kp
#SBATCH --partition=ucgd-kp
#SBATCH --time=84:00:00
<BLANKLINE>
<BLANKLINE>
<BLANKLINE>
__script__


>>> job_id = s.run("rm -f aaa; sleep 10; echo 213 > aaa", name_addition="", tries=1)

>>> job = s.run("cat aaa; rm aaa", name_addition="", tries=1, depends_on=[job_id])

"""
from __future__ import print_function

import sys
import os
import subprocess
import tempfile
import atexit
import hashlib
import datetime
from itertools import product
import re

TMPL = """\
#!/bin/bash

#SBATCH -e {log_dir}/{name}.%J.err
#SBATCH -o {log_dir}/{name}.%J.out
#SBATCH -J {name}

{header}

{bash_setup}

__script__"""


def tmp(suffix=".sh"):
    t = tempfile.mktemp(suffix=suffix)
    atexit.register(os.unlink, t)
    return t


class Slurm(object):
    def __init__(self, name, slurm_kwargs=None, tmpl=None,
                 date_in_name=True, scripts_dir="slurm-scripts",
                 log_dir='logs', bash_strict=True):
        if slurm_kwargs is None:
            slurm_kwargs = {}
        if tmpl is None:
            tmpl = TMPL
        self.log_dir = log_dir
        self.bash_strict = bash_strict

        header = []
        if 'time' not in slurm_kwargs.keys():
            slurm_kwargs['time'] = '84:00:00'
        for k, v in slurm_kwargs.items():
            if len(k) > 1:
                k = "--" + k + "="
            else:
                k = "-" + k + " "
            header.append("#SBATCH %s%s" % (k, v))

        # add bash setup list to collect bash script config
        bash_setup = []
        if bash_strict:
            bash_setup.append("set -eo pipefail -o nounset")

        self.header = "\n".join(header)
        self.bash_setup = "\n".join(bash_setup)
        self.name = "".join(x for x in name.replace(
            " ", "-") if x.isalnum() or x == "-")
        self.tmpl = tmpl
        self.slurm_kwargs = slurm_kwargs
        if scripts_dir is not None:
            self.scripts_dir = os.path.abspath(scripts_dir)
        else:
            self.scripts_dir = None
        self.date_in_name = bool(date_in_name)

    def __str__(self):
        return self.tmpl.format(name=self.name, header=self.header,
                                log_dir=self.log_dir,
                                bash_setup=self.bash_setup)

    def _tmpfile(self):
        if self.scripts_dir is None:
            return tmp()
        else:
            for _dir in [self.scripts_dir, self.log_dir]:
                if not os.path.exists(_dir):
                    os.makedirs(_dir)
            return "%s/%s.sh" % (self.scripts_dir, self.name)

    def run(self, command, name_addition=None, cmd_kwargs=None,
            _cmd="sbatch", tries=1, depends_on=None):
        """
        command: a bash command that you want to run
        name_addition: if not specified, the sha1 of the command to run
                       appended to job name. if it is "date", the yyyy-mm-dd
                       date will be added to the job name.
        cmd_kwargs: dict of extra arguments to fill in command
                   (so command itself can be a template).
        _cmd: submit command (change to "bash" for testing).
        tries: try to run a job either this many times or until the first
               success.
        depends_on: job ids that this depends on before it is run (users 'afterok')
        """
        if name_addition is None:
            name_addition = hashlib.sha1(command.encode("utf-8")).hexdigest()

        if self.date_in_name:
            name_addition += "-" + str(datetime.date.today())
        name_addition = name_addition.strip(" -")

        if cmd_kwargs is None:
            cmd_kwargs = {}

        n = self.name
        self.name = self.name.strip(" -")
        self.name += ("-" + name_addition.strip(" -"))
        args = []
        for k, v in cmd_kwargs.items():
            args.append("export %s=%s" % (k, v))
        args = "\n".join(args)

        tmpl = str(self).replace("__script__", args + "\n###\n" + command)
        if depends_on is None or (len(depends_on) == 1 and depends_on[0] is None):
            depends_on = []

        with open(self._tmpfile(), "w") as sh:
            sh.write(tmpl)

        job_id = None
        for itry in range(1, tries + 1):
            args = [_cmd]
            args.extend([("--dependency=afterok:%d" % int(d))
                         for d in depends_on])
            if itry > 1:
                mid = "--dependency=afternotok:%d" % job_id
                args.append(mid)
            args.append(sh.name)
            res = subprocess.check_output(args).strip()
            print(res, file=sys.stderr)
            self.name = n
            if not res.startswith(b"Submitted batch"):
                return None
            j_id = int(res.split()[-1])
            if itry == 1:
                job_id = j_id
        return job_id


def run_grid_search(name, command, name_addition=None, cmd_kwargs=None,
                    _cmd="sbatch", tries=1, depends_on=None, slurm_kwargs=None, tmpl=None,
                    date_in_name=True, scripts_dir="slurm-scripts",
                    log_dir='logs', bash_strict=True):
    pattern = re.compile(r'{{(.*?)}}')
    grid_searches_idx = []
    grid_searches_lst = []

    for m in re.finditer(pattern, command):
        grid_searches_lst.append(command[m.span()[0] + 2:m.span()[1] - 2].split(","))
        grid_searches_idx.append(m.span())

    prod = product(*grid_searches_lst)
    while True:
        elm = next(prod, None)
        if elm:
            command_tmp = command
            for i, e in reversed(list(enumerate(elm))):
                start, end = grid_searches_idx[i]
                command_tmp = command_tmp[:start] + e + command_tmp[end:]
            print(command_tmp)
            # todo: slurm + run
            # todo: name add something output add something?
            slurm = Slurm(name=name, slurm_kwargs=slurm_kwargs, tmpl=tmpl,
                          date_in_name=date_in_name, scripts_dir=scripts_dir,
                          log_dir=log_dir, bash_strict=bash_strict)

            slurm.run(command_tmp, name_addition=name_addition, cmd_kwargs=cmd_kwargs,
                      _cmd=_cmd, tries=tries, depends_on=depends_on)

            pass
        else:
            break


if __name__ == "__main__":
    import doctest

    doctest.testmod()
