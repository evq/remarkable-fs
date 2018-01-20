import fnmatch
import json
import time
import os.path
import itertools
import traceback
from uuid import uuid4
from lazy import lazy

class Node(object):
    def __init__(self, root, id, metadata):
        self.root = root
        self.id = id
        self.metadata = metadata

    def __repr__(self):
        return "%s(%s, %s)" % \
            (type(self).__name__,
             self.id,
             self.name)

    def link(self):
        self.parent = self.root.find_node(self.metadata["parent"])
        if self.parent is not None:
            self.parent.add_child(self)

    def get_times_from(self, file):
        st = self.root.sftp.stat(file)
        self.size = st.st_size
        self.atime = st.st_atime
        self.mtime = st.st_mtime

    def _rw(name):
        def get(self):
            return self.metadata[name]
        def set(self, val):
            self.metadata["synced"] = False
            self.metadata["metadatamodified"] = True
            self.metadata["version"] += 1
            self.metadata[name] = val
        return property(fget=get, fset=set)
    
    name = _rw("visibleName")
    deleted = _rw("deleted")
    data_modified = _rw("modified")

    @property
    def file_name(self):
        return self.name

    @property
    def visible(self):
        return not self.deleted

    @property
    def metadata_modified(self):
        return self.metadata["metadatamodified"]

    def save(self):
        if self.metadata_modified:
            self.root.write_metadata(self.id, self.metadata)

    def rename(self, parent, name):
        if self.parent == parent and self.name == name:
            return

        self.parent.remove_child(self)
        self.name = name
        self.metadata["parent"] = parent.id
        self.parent = parent
        self.parent.add_child(self)
        self.save()
    
    def delete(self):
        self.parent.remove_child(self)
        self.deleted = True
        self.save()

class Collection(Node):
    def __init__(self, root, id, metadata):
        super(Collection, self).__init__(root, id, metadata)
        if id is not None: self.get_times_from(id + ".metadata")
        self.children = {}
        self.children_pathnames = {}

    def add_child(self, child):
        # Remove invalid chars
        name = child.file_name.replace("/", "-")

        # Disambiguate duplicate names e.g. Foo/bar, Foo-bar
        if name in self.children:
            for n in itertools.count(2):
                x = "%s (%d)" % (name, n)
                if x not in self.children:
                    name = x
                    break

        self.children[name] = child
        self.children_pathnames[child] = name

    def remove_child(self, child):
        name = self.children_pathnames[child]
        del self.children[name]
        del self.children_pathnames[child]

    def __repr__(self):
        return "%s(%s, %s, %s)" % \
            (type(self).__name__,
             self.id,
             self.name,
             self.children)

    def __getitem__(self, key):
        return self.children[key]

    def __iter__(self):
        return iter(self.children)

    def __contains__(self, item):
        return item in self.children

    def get(self, key):
        return self.children.get(key)

    def items(self):
        return self.children.items()

    @staticmethod
    def node_type():
        return "CollectionType"

class DocumentRoot(Collection):
    def __init__(self, sftp):
        super(DocumentRoot, self).__init__(self, None, None)
        self.id = ""
        self.sftp = sftp
        self.nodes = {"": self}

        for path in fnmatch.filter(sftp.listdir(), '*.metadata'):
            id, _ = os.path.splitext(path)
            self.load_node_without_linking(id)

        self.link_nodes()

    @property
    def name(self):
        return "ROOT"

    def link(self):
        pass

    def load_node(self, id):
        node = self.load_node_without_linking(id)
        if node is not None: node.link()
        return node

    def load_node_without_linking(self, id):
        classes = [Document, Collection]
        classes_dict = {cls.node_type(): cls for cls in classes}

        metadata = json.loads(self.sftp.open(id + ".metadata").read().decode("utf-8"))
        try:
            cls = classes_dict[metadata["type"]]
        except KeyError:
            cls = Node
          
        try:
            node = cls(self, id, metadata)
            if node.visible:
                self.nodes[id] = node
                return node
        except IOError:
            traceback.print_exc()

    def link_nodes(self):
        for node in self.nodes.values():
            node.link()

    def find_node(self, id):
        return self.nodes.get(id)

    def read_json(self, file):
        return json.loads(self.sftp.open(file).read().decode("utf-8"))
        
    def write_json(self, file, value):
        self.sftp.open(file, "w").write(json.dumps(value))

    def read_metadata(self, id):
        return self.read_json(id + ".metadata")

    def write_metadata(self, id, metadata):
        self.write_json(id + ".metadata", metadata)

class Document(Node):
    def __init__(self, root, id, metadata):
        super(Document, self).__init__(root, id, metadata)
        self.content = self.root.read_json(id + ".content")
        if self.visible:
            self.get_times_from(self.raw_file_name)

    @property
    def file_type(self):
        return self.content["fileType"]

    @property
    def raw_file_name(self):
        return self.id + "." + self.file_type

    @property
    def file_name(self):
        return self.name + "." + self.file_type

    @lazy
    def file(self):
        return self.root.sftp.open(self.raw_file_name)
    
    def read(self):
        return self.file.read()
    
    def read_chunk(self, offset, length):
        [str] = self.file.readv([(offset, length)])
        return str
    
    @property
    def visible(self):
        return self.file_type != ""

    @staticmethod
    def node_type():
        return "DocumentType"

def new_collection(root, name, parent):
    id = new_id()
    metadata = initial_metadata(Collection.node_type(), name, parent)
    root.write_metadata(id, metadata)
    return root.load_node(id)

def new_document(root, name, parent, contents):
    if contents.startswith("%PDF"):
        filetype = "pdf"
    elif contents.startswith("PK"):
        filetype = "epub"
    else:
        raise RuntimeError("Only PDF and epub format files supported")

    id = new_id()
    metadata = initial_metadata(Document.node_type(), name, parent)
    root.write_metadata(id, metadata)

    content = {"fileType": filetype}
    root.write_json(id + ".content", content)
    root.sftp.open(id + "." + filetype, "w").write(contents)
    return root.load_node(id)

def new_id():
    return str(uuid4())

def initial_metadata(node_type, name, parent):
    return {
        "deleted": False,
        "lastModified": str(int(time.time()*1000)),
        "metadatamodified": True,
        "modified": True,
        "parent": parent.id,
        "pinned": False,
        "synced": True,
        "type": node_type,
        "version": 1,
        "visibleName": name
    }
