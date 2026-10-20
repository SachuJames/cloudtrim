"""cloudtrim: find wasted Kubernetes resources.

Reads a snapshot of requested vs measured container usage, flags the
containers that ask for far more than they use, and puts a rough monthly
price on the difference. Reads and reports only, it never changes a
cluster.
"""

__version__ = "0.2.0"
