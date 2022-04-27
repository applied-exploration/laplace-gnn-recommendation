from torch_geometric.transforms import RandomLinkSplit
from torch_geometric.data import HeteroData
from data.types import DataLoaderConfig, ArticleIdMap, CustomerIdMap
import torch
import json
from typing import Tuple
import torch_geometric.transforms as T
from torch_geometric.loader import NeighborLoader, LinkNeighborLoader
from data.dataset import GraphDataset
from torch.utils.data import DataLoader

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def create_dataloaders(
    config: DataLoaderConfig,
) -> Tuple[
    LinkNeighborLoader,
    LinkNeighborLoader,
    LinkNeighborLoader,
    CustomerIdMap,
    ArticleIdMap,
]:
    data_dir = "data/derived/"
    train_dataset = GraphDataset(
        edge_dir=data_dir + "edges_train.pt", graph_dir=data_dir + "train_graph.pt"
    )
    val_dataset = GraphDataset(
        edge_dir=data_dir + "edges_val.pt", graph_dir=data_dir + "val_graph.pt"
    )
    test_dataset = GraphDataset(
        edge_dir=data_dir + "edges_test.pt", graph_dir=data_dir + "test_graph.pt"
    )

    # Add a reverse ('article', 'rev_buys', 'customer') relation for message passing:
    # data = T.ToUndirected()(data)

    class PadSequence:
        def __call__(self, batch):
            # Let's assume that each element in "batch" is a tuple (data, label).
            # Sort the batch in the descending order
            sorted_batch = sorted(batch, key=lambda x: x[0].shape[0], reverse=True)
            # Get each sequence and pad it
            sequences = [x[0] for x in sorted_batch]
            sequences_padded = torch.nn.utils.rnn.pad_sequence(
                sequences, batch_first=True, padding_value=-1.0
            )
            # Also need to store the length of each sequence
            # This is later needed in order to unpad the sequences
            lengths = torch.LongTensor([len(x) for x in sequences])
            # Don't forget to grab the labels of the *sorted* batch

            user_features = torch.stack([x[1] for x in sorted_batch], dim=0)
            article_features = torch.stack([x[2] for x in sorted_batch], dim=0)
            return sequences_padded, lengths, user_features, article_features

    train_loader = DataLoader(
        train_dataset, batch_size=2, shuffle=False, collate_fn=PadSequence()
    )
    val_loader = DataLoader(train_dataset, batch_size=2, shuffle=False)
    test_loader = DataLoader(train_dataset, batch_size=2, shuffle=False)
    next_item = next(iter(train_loader))
    data = train_dataset.graph

    customer_id_map = read_json("data/derived/customer_id_map_forward.json")
    article_id_map = read_json("data/derived/article_id_map_forward.json")

    return (
        train_loader,
        val_loader,
        test_loader,
        customer_id_map,
        article_id_map,
        data,
    )


def read_json(filename: str):
    with open(filename) as f_in:
        return json.load(f_in)
