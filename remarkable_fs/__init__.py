from remarkable_fs.connection import connect
from remarkable_fs.documents import DocumentRoot
from remarkable_fs.fs import mount
import sys

try:
    import __builtin__
    raw_input = __builtin__.raw_input
except:
    raw_input = input

def main(argv = sys.argv):
    if len(argv) == 2:
        mount_point = argv[1]
    else:
        mount_point = raw_input("Mount point: ")

    with connect() as conn:
        mount(mount_point, DocumentRoot(conn.sftp))
