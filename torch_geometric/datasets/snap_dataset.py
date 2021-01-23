import os
import os.path as osp
import json
import torch
import pandas
import numpy as np
from dateutil.parser import parse, parserinfo
from torch_sparse import coalesce
from torch_geometric.data import (Data, InMemoryDataset, download_url,
                                  extract_gz, extract_tar, extract_zip)
from torch_geometric.data.makedirs import makedirs
from torch_geometric.utils import to_undirected


def read_ca_web_amazon_p2p_roadnet(files, name):
    edge_index = pandas.read_csv(files[0], sep='\t', header=None,
                                 skiprows=4)
    edge_index = torch.from_numpy(edge_index.to_numpy()).t()

    idx_assoc = {}
    for i, j in enumerate(torch.unique(edge_index).tolist()):
        idx_assoc[j] = i

    edge_index = edge_index.flatten()
    for i, e in enumerate(edge_index.tolist()):
        edge_index[i] = idx_assoc[e]
    edge_index = edge_index.view(2, -1)
    num_nodes = edge_index.max() + 1

    if 'ca-' in name:
        edge_index = to_undirected(edge_index, num_nodes)

    return [Data(edge_index=edge_index, num_nodes=num_nodes)]


def read_cit(files, name):
    if name == 'patents':
        edge_file = files[0]
        x = None
    elif name == 'hepph' or name == 'hepth':
        edge_file = files[1]
        d = pandas.read_csv(files[0], sep='\t', header=None, skiprows=1)
        idx, x = d[[0]], d[[1]]

        # parse date and calculate difference in days to oldest date
        x = x.to_numpy().flatten().tolist()
        for i in range(len(x)):
            x[i] = parse(x[i], parserinfo(yearfirst=True))
        oldest_date = min(x)
        for i in range(len(x)):
            x[i] = (x[i] - oldest_date).days
        x = torch.tensor(x)

        idx = torch.from_numpy(idx.to_numpy()).flatten()
        idx_assoc = {}
        for i, j in enumerate(idx.tolist()):
            idx_assoc[j] = i

    edge_index = pandas.read_csv(edge_file, sep='\t', header=None,
                                 skiprows=4)
    edge_index = torch.from_numpy(edge_index.to_numpy()).t()

    if name == 'patents':
        idx_assoc = {}
        for i, j in enumerate(torch.unique(edge_index).tolist()):
            idx_assoc[j] = i

    edge_index = edge_index.flatten()
    for i, e in enumerate(edge_index.tolist()):
        try:
            edge_index[i] = idx_assoc[e]

        # handle nodes, which don't have features
        except KeyError:
            max_assoc = max(list(idx_assoc.values()))
            idx_assoc[e] = max_assoc + 1
            edge_index[i] = idx_assoc[e]
            x = torch.cat((x, torch.tensor([-1])))

    edge_index = edge_index.view(2, -1)
    num_nodes = edge_index.max() + 1

    return [Data(x=x, edge_index=edge_index, num_nodes=num_nodes)]


def read_email(files, name):
    if name == 'eu-core':
        y = pandas.read_csv(files[0], sep=' ', header=None)
        y = torch.from_numpy(y.to_numpy())
        y = y[:, 1].to(torch.long)

        edge_index = pandas.read_csv(files[1], sep=' ', header=None)
        edge_index = torch.from_numpy(edge_index.to_numpy()).t()

        assert torch.eq(torch.unique(edge_index.flatten()),
                        torch.arange(0, edge_index.max() + 1)).all()
        num_nodes = edge_index.max() + 1

        return [Data(edge_index=edge_index, num_nodes=num_nodes, y=y)]

    elif name == 'enron' or name == 'euall':
        edge_index = pandas.read_csv(files[0], sep='\t', header=None,
                                     skiprows=4)
        edge_index = torch.from_numpy(edge_index.to_numpy()).t()

        assert torch.eq(torch.unique(edge_index.flatten()),
                        torch.arange(0, edge_index.max() + 1)).all()
        num_nodes = edge_index.max() + 1

        if name == 'enron':
            edge_index = to_undirected(edge_index, num_nodes)

        return [Data(edge_index=edge_index, num_nodes=num_nodes)]


def read_com(files, name):
    for file in files:
        if '.ungraph' in file:
            edge_index = pandas.read_csv(file, sep='\t', header=None,
                                         skiprows=4)
            edge_index = torch.from_numpy(edge_index.to_numpy()).t()
            # there are multiple duplicated edges

            idx_assoc = {}
            for i, j in enumerate(torch.unique(edge_index).tolist()):
                idx_assoc[j] = i

            edge_index = edge_index.flatten()
            for i, e in enumerate(edge_index.tolist()):
                edge_index[i] = idx_assoc[e]
            edge_index = edge_index.view(2, -1)
            num_nodes = edge_index.max() + 1
            edge_index = to_undirected(edge_index, num_nodes)

    for file in files:
        if '.all' in file:
            communities = []
            communities_batch = []
            with open(file, 'r') as f:
                for i, com in enumerate(f.read().split('\n')[:-1]):
                    com = [idx_assoc[int(c)] for c in com.split()]
                    communities += com
                    communities_batch += [i] * len(com)
            communities = torch.tensor(communities)
            communities_batch = torch.tensor(communities_batch)

    data = Data(edge_index=edge_index, num_nodes=num_nodes,
                communities=communities, communities_batch=communities_batch)
    return [data]


class EgoData(Data):
    def __inc__(self, key, item):
        if key == 'circle':
            return self.num_nodes
        elif key == 'circle_batch':
            return item.max().item() + 1 if item.numel() > 0 else 0
        else:
            return super(EgoData, self).__inc__(key, item)


def read_ego(files, name):
    all_featnames = []
    for i in range(4, len(files), 5):
        featnames_file = files[i]
        with open(featnames_file, 'r') as f:
            featnames = f.read().split('\n')[:-1]
            featnames = [' '.join(x.split(' ')[1:]) for x in featnames]
            all_featnames += featnames
    all_featnames = sorted(list(set(all_featnames)))
    all_featnames = {key: i for i, key in enumerate(all_featnames)}

    data_list = []
    for i in range(0, len(files), 5):
        circles_file = files[i]
        edges_file = files[i + 1]
        egofeat_file = files[i + 2]
        feat_file = files[i + 3]
        featnames_file = files[i + 4]

        x = pandas.read_csv(feat_file, sep=' ', header=None, dtype=np.float32)
        x = torch.from_numpy(x.values)

        idx, x = x[:, 0].to(torch.long), x[:, 1:].to(torch.float)
        idx_assoc = {}
        for i, j in enumerate(idx.tolist()):
            idx_assoc[j] = i

        circles = []
        circles_batch = []
        with open(circles_file, 'r') as f:
            for i, circle in enumerate(f.read().split('\n')[:-1]):
                circle = [int(idx_assoc[int(c)]) for c in circle.split()[1:]]
                circles += circle
                circles_batch += [i] * len(circle)
        circle = torch.tensor(circles)
        circle_batch = torch.tensor(circles_batch)

        edge_index = pandas.read_csv(edges_file, sep=' ', header=None,
                                     dtype=np.int64)
        edge_index = torch.from_numpy(edge_index.values).t()
        edge_index = edge_index.flatten()
        for i, e in enumerate(edge_index.tolist()):
            edge_index[i] = idx_assoc[e]
        edge_index = edge_index.view(2, -1)

        if name == 'facebook':
            # undirected edges
            edge_index = to_undirected(edge_index, x.size(0))

        row, col = edge_index

        x_ego = pandas.read_csv(egofeat_file, sep=' ', header=None,
                                dtype=np.float32)
        x_ego = torch.from_numpy(x_ego.values)

        row_ego = torch.full((x.size(0), ), x.size(0), dtype=torch.long)
        col_ego = torch.arange(x.size(0))

        # Ego node should be connected to every other node.
        row = torch.cat([row, row_ego, col_ego], dim=0)
        col = torch.cat([col, col_ego, row_ego], dim=0)
        edge_index = torch.stack([row, col], dim=0)

        x = torch.cat([x, x_ego], dim=0)

        # Reorder `x` according to `featnames` ordering.
        x_all = torch.zeros(x.size(0), len(all_featnames))
        with open(featnames_file, 'r') as f:
            featnames = f.read().split('\n')[:-1]
            featnames = [' '.join(x.split(' ')[1:]) for x in featnames]
        indices = [all_featnames[featname] for featname in featnames]
        x_all[:, torch.tensor(indices)] = x

        edge_index, _ = coalesce(edge_index, None, x.size(0), x.size(0))
        data = Data(x=x_all, edge_index=edge_index, circle=circle,
                    circle_batch=circle_batch)

        data_list.append(data)

    return data_list


def read_soc(files, name):
    if 'sign-bitcoin-' in name:
        d = pandas.read_csv(files[0], header=None, skiprows=0)
        edge_index = torch.from_numpy(d[[0, 1]].to_numpy()).t()
        edge_attr = torch.from_numpy(d[[2, 3]].to_numpy())

        idx = torch.unique(edge_index.flatten())
        idx_assoc = torch.full((edge_index.max() + 1, ), -1, dtype=torch.long)
        idx_assoc[idx] = torch.arange(idx.size(0))

        edge_index = idx_assoc[edge_index]
        num_nodes = edge_index.max().item() + 1

        return [Data(edge_index=edge_index,
                     edge_attr=edge_attr,
                     num_nodes=num_nodes)]
    else:
        skiprows = 4
        if name == 'pokec':
            skiprows = 0

        edge_index = pandas.read_csv(files[0], sep='\t', header=None,
                                     skiprows=skiprows, dtype=np.int64)
        edge_index = torch.from_numpy(edge_index.values).t()
        num_nodes = edge_index.max().item() + 1
        edge_index, _ = coalesce(edge_index, None, num_nodes, num_nodes)

        return [Data(edge_index=edge_index, num_nodes=num_nodes)]


def read_wiki(files, name):
    if name == 'vote' or name == 'talk':
        edge_file = files[0]
        skiprows = 4
        sep = '\t'
    elif name == 'topcats':
        edge_file = files[1]
        skiprows = 0
        sep = ' '

    edge_index = pandas.read_csv(edge_file, sep=sep, header=None,
                                 skiprows=skiprows, dtype=np.int64)
    edge_index = torch.from_numpy(edge_index.values).t()

    idx = torch.unique(edge_index.flatten())
    idx_assoc = torch.full((edge_index.max() + 1, ), -1, dtype=torch.long)
    idx_assoc[idx] = torch.arange(idx.size(0))

    edge_index = idx_assoc[edge_index]
    num_nodes = edge_index.max().item() + 1
    edge_index, _ = coalesce(edge_index, None, num_nodes, num_nodes)

    if name == 'topcats':
        cat_file = files[0]

        with open(cat_file, 'r') as f:
            lines = f.readlines()
        categories = []
        categories_batch = []

        for i, line in enumerate(lines):
            category = [int(idx_assoc[int(c)]) for c in line.split()[1:]]
            categories += category
            categories_batch += [i] * len(category)
        categories = torch.tensor(categories)
        categories_batch = torch.tensor(categories_batch)

        return [Data(edge_index=edge_index, num_nodes=num_nodes,
                     categories=categories, categories_batch=categories_batch)]

    else:
        return [Data(edge_index=edge_index, num_nodes=num_nodes)]


def read_gemsec(files, name):
    data_list = []
    for file in files:
        if 'edges' in file:
            edge_index = pandas.read_csv(file, header=None, skiprows=1)
            edge_index = torch.from_numpy(edge_index.to_numpy()).t()

            assert torch.eq(torch.unique(edge_index.flatten()),
                            torch.arange(0, edge_index.max() + 1)).all()
            num_nodes = edge_index.max().item() + 1

            # undirected edges
            edge_index = to_undirected(edge_index, num_nodes)

            edge_index, _ = coalesce(edge_index, None, num_nodes, num_nodes)
            data = Data(edge_index=edge_index, num_nodes=num_nodes)
            data_list.append(data)

    return data_list


def read_musae(paths, name):
    if name in ['twitch', 'wiki']:
        data_list = []
        for path in paths:
            if osp.isdir(path):
                _paths = [osp.join(path, file) for file in os.listdir(path)]
                graph_name = osp.basename(osp.dirname(_paths[0]))
                data = read_musae_helper(_paths, name, graph_name)
                data_list.append(data)
        return data_list
    else:
        data = read_musae_helper(paths, name)
        return [data]


def read_musae_helper(paths, name, data_name=None):
    x, edge_index, y, num_nodes = None, None, None, None

    for file in paths:
        if 'target' in file:
            y = pandas.read_csv(osp.join(path, file),
                                header=None, skiprows=1)

            # drop columns with string attribute
            if name == 'github':
                y = y.drop([1], axis=1)
            if name == 'facebook':
                y = y.drop([2, 3], axis=1)

            y = torch.from_numpy(y.to_numpy(dtype=np.int))

        elif 'edges' in file:
            edge_index = pandas.read_csv(osp.join(path, file),
                                         header=None, skiprows=1)
            edge_index = torch.from_numpy(edge_index.to_numpy()).t()
            assert torch.eq(torch.unique(edge_index.flatten()),
                            torch.arange(0, edge_index.max() + 1)).all()
            num_nodes = edge_index.max() + 1

            # undirected edges
            edge_index = to_undirected(edge_index, num_nodes)

        elif file.endswith('.json'):
            with open(osp.join(path, file)) as f:
                dict = json.load(f)

            if name in ['twitch', 'wiki']:
                if name == 'twitch':
                    num_features = 3170
                elif name == 'wiki':
                    num_features = 13183
                else:
                    raise ValueError
                x = torch.zeros(len(dict), num_features)
                for i in range(len(dict)):
                    for f in dict[str(i)]:
                        x[i][f] = 1
            else:
                features = np.unique(np.asarray(
                           [i for _list in list(dict.values())
                            for i in _list]))
                f_assoc, num_features = {}, 0
                for i, j in enumerate(features):
                    f_assoc[j] = i
                    num_features = i+1

                x = torch.zeros(len(dict), num_features)
                for i in range(len(dict)):
                    for f in dict[str(i)]:
                        x[i][f_assoc[f]] = 1

    return Data(x=x, edge_index=edge_index, y=y,
                num_nodes=num_nodes, name=data_name)


class SNAPDataset(InMemoryDataset):
    r"""A variety of graph datasets collected from `SNAP at Stanford University
    <https://snap.stanford.edu/data>`_.

    Args:
        root (string): Root directory where the dataset should be saved.
        name (string): The name of the dataset.
        transform (callable, optional): A function/transform that takes in an
            :obj:`torch_geometric.data.Data` object and returns a transformed
            version. The data object will be transformed before every access.
            (default: :obj:`None`)
        pre_transform (callable, optional): A function/transform that takes in
            an :obj:`torch_geometric.data.Data` object and returns a
            transformed version. The data object will be transformed before
            being saved to disk. (default: :obj:`None`)
        pre_filter (callable, optional): A function that takes in an
            :obj:`torch_geometric.data.Data` object and returns a boolean
            value, indicating whether the data object should be included in the
            final dataset. (default: :obj:`None`)
    """

    url = 'https://snap.stanford.edu/data'

    available_datasets = {
        'ego-facebook': ['facebook.tar.gz'],
        'ego-gplus': ['gplus.tar.gz'],
        'ego-twitter': ['twitter.tar.gz'],
        'soc-epinions1': ['soc-Epinions1.txt.gz'],
        'soc-livejournal1': ['soc-LiveJournal1.txt.gz'],
        'soc-pokec': ['soc-pokec-relationships.txt.gz'],
        'soc-slashdot0811': ['soc-Slashdot0811.txt.gz'],
        'soc-slashdot0922': ['soc-Slashdot0902.txt.gz'],
        'soc-sign-bitcoin-otc': ['soc-sign-bitcoinotc.csv.gz'],
        'soc-sign-bitcoin-alpha': ['soc-sign-bitcoinalpha.csv.gz'],
        'wiki-vote': ['wiki-Vote.txt.gz'],
        'wiki-topcats': ['wiki-topcats.txt.gz',
                         'wiki-topcats-categories.txt.gz'],
        'wiki-talk': ['wiki-Talk.txt.gz'],
        'gemsec-deezer': ['gemsec_deezer_dataset.tar.gz'],
        'gemsec-facebook': ['gemsec_facebook_dataset.tar.gz'],
        'musae-twitch': ['twitch.zip'],
        'musae-facebook': ['facebook_large.zip'],
        'musae-github': ['git_web_ml.zip'],
        'musae-wiki': ['wikipedia.zip'],
        'com-livejournal': ['com-lj.ungraph.txt.gz',
                            'com-lj.all.cmty.txt.gz'],
        'com-friendster': ['com-friendster.ungraph.txt.gz',
                           'com-friendster.all.cmty.txt.gz'],
        'com-orkut': ['com-orkut.ungraph.txt.gz',
                      'com-orkut.all.cmty.txt.gz'],
        'com-youtube': ['com-youtube.ungraph.txt.gz',
                        'com-youtube.all.cmty.txt.gz'],
        'com-dblp': ['com-dblp.ungraph.txt.gz',
                     'com-dblp.all.cmty.txt.gz'],
        'com-amazon': ['com-amazon.ungraph.txt.gz',
                       'com-amazon.all.cmty.txt.gz'],
        'email-eu-core': ['email-Eu-core.txt.gz',
                          'email-Eu-core-department-labels.txt.gz'],
        'email-euall': ['email-EuAll.txt.gz'],
        'email-enron': ['email-Enron.txt.gz'],
        'cit-hepph': ['cit-HepPh.txt.gz',
                      'cit-HepPh-dates.txt.gz'],
        'cit-hepth': ['cit-HepTh.txt.gz',
                      'cit-HepTh-dates.txt.gz'],
        'cit-patents': ['cit-Patents.txt.gz'],
        'ca-astroph': ['ca-AstroPh.txt.gz'],
        'ca-condmat': ['ca-CondMat.txt.gz'],
        'ca-grqc': ['ca-GrQc.txt.gz'],
        'ca-hepph': ['ca-HepPh.txt.gz'],
        'ca-hepth': ['ca-HepTh.txt.gz'],
        'web-berkstan': ['web-BerkStan.txt.gz'],
        'web-google': ['web-Google.txt.gz'],
        'web-notredame': ['web-NotreDame.txt.gz'],
        'web-stanford': ['web-Stanford.txt.gz'],
        'amazon-0302': ['amazon0302.txt.gz'],
        'amazon-0312': ['amazon0312.txt.gz'],
        'amazon-0505': ['amazon0505.txt.gz'],
        'amazon-0601': ['amazon0601.txt.gz'],
        'p2p-gnutella04': ['p2p-Gnutella04.txt.gz'],
        'p2p-gnutella05': ['p2p-Gnutella05.txt.gz'],
        'p2p-gnutella06': ['p2p-Gnutella06.txt.gz'],
        'p2p-gnutella08': ['p2p-Gnutella08.txt.gz'],
        'p2p-gnutella09': ['p2p-Gnutella09.txt.gz'],
        'p2p-gnutella24': ['p2p-Gnutella24.txt.gz'],
        'p2p-gnutella25': ['p2p-Gnutella25.txt.gz'],
        'p2p-gnutella30': ['p2p-Gnutella30.txt.gz'],
        'p2p-gnutella31': ['p2p-Gnutella31.txt.gz'],
        'roadnet-ca': ['roadNet-CA.txt.gz'],
        'roadnet-pa': ['roadNet-PA.txt.gz'],
        'roadnet-tx': ['roadNet-TX.txt.gz'],
    }

    big_datasets = ['com-livejournal',
                    'com-friendster',
                    'com-orkut',
                    'com-youtube',
                    'com-dblp',
                    'com-amazon']

    def __init__(self, root, name, transform=None, pre_transform=None,
                 pre_filter=None):
        self.name = name.lower()
        assert self.name in self.available_datasets.keys()

        if self.name in self.big_datasets:
            self.url = 'https://snap.stanford.edu/data/bigdata/communities/'

        super(SNAPDataset, self).__init__(root, transform, pre_transform,
                                          pre_filter)
        self.data, self.slices = torch.load(self.processed_paths[0])

    @property
    def raw_dir(self):
        return osp.join(self.root, self.name, 'raw')

    @property
    def processed_dir(self):
        return osp.join(self.root, self.name, 'processed')

    @property
    def processed_file_names(self):
        return 'data.pt'

    def _download(self):
        if osp.isdir(self.raw_dir) and len(os.listdir(self.raw_dir)) > 0:
            return

        makedirs(self.raw_dir)
        self.download()

    def download(self):
        for name in self.available_datasets[self.name]:
            path = download_url('{}/{}'.format(self.url, name), self.raw_dir)
            print(path)
            if name.endswith('.tar.gz'):
                extract_tar(path, self.raw_dir)
            elif name.endswith('.gz'):
                extract_gz(path, self.raw_dir)
            elif name.endswith('.zip'):
                extract_zip(path, self.raw_dir)
            os.unlink(path)

    def process(self):
        raw_dir = self.raw_dir
        filenames = os.listdir(self.raw_dir)
        if len(filenames) == 1 and osp.isdir(osp.join(raw_dir, filenames[0])):
            raw_dir = osp.join(raw_dir, filenames[0])

        raw_files = sorted([osp.join(raw_dir, f) for f in os.listdir(raw_dir)])
        print('Raw Files:', raw_files, '\n')

        if self.name[:4] == 'ego-':
            data_list = read_ego(raw_files, self.name[4:])
        elif self.name[:4] == 'soc-':
            data_list = read_soc(raw_files, self.name[4:])
        elif self.name[:5] == 'wiki-':
            data_list = read_wiki(raw_files, self.name[5:])
        elif self.name[:7] == 'gemsec-':
            data_list = read_gemsec(raw_files, self.name[7:])
        elif self.name[:6] == 'musae-':
            data_list = read_musae(raw_files, self.name[6:])
        elif self.name[:4] == 'com-':
            data_list = read_com(raw_files, self.name[4:])
        elif self.name[:6] == 'email-':
            data_list = read_email(raw_files, self.name[6:])
        elif self.name[:4] == 'cit-':
            data_list = read_cit(raw_files, self.name[4:])
        elif self.name[:3] == 'ca-':
            data_list = read_ca_web_amazon_p2p_roadnet(raw_files, self.name)
        elif self.name[:4] == 'web-':
            data_list = read_ca_web_amazon_p2p_roadnet(raw_files, self.name)
        elif self.name[:7] == 'amazon-':
            data_list = read_ca_web_amazon_p2p_roadnet(raw_files, self.name)
        elif self.name[:4] == 'p2p-':
            data_list = read_ca_web_amazon_p2p_roadnet(raw_files, self.name)
        elif self.name[:8] == 'roadnet-':
            data_list = read_ca_web_amazon_p2p_roadnet(raw_files, self.name)
        else:
            raise NotImplementedError

        if len(data_list) > 1 and self.pre_filter is not None:
            data_list = [data for data in data_list if self.pre_filter(data)]

        if self.pre_transform is not None:
            data_list = [self.pre_transform(data) for data in data_list]

        torch.save(self.collate(data_list), self.processed_paths[0])

    def __repr__(self):
        return 'SNAP-{}({})'.format(self.name, len(self))


if __name__ == '__main__':
    dataset_name = 'roadNet-TX'
    path = osp.join(osp.dirname(osp.realpath(__file__)),
                    '..', '..', 'data', dataset_name)
    dataset = SNAPDataset(path, dataset_name)
    data = dataset[0]
    print(data)
