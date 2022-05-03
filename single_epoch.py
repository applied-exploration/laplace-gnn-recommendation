from config import Config
from typing import Union, Tuple

import torch
from torch import Tensor
from torch.nn import Module
from torch.optim import Optimizer
from torch_geometric.data import HeteroData, Data
from tqdm import tqdm
from sklearn.metrics import roc_auc_score
from torch_geometric.loader import NeighborLoader, LinkNeighborLoader
from utils.metrics_encoder_decoder import get_metrics_universal
from utils.get_info import select_properties

import cProfile as profile
import pstats


def train(
    train_data: Union[HeteroData, Data],
    model: Module,
    optimizer: Optimizer,
) -> float:

    x, edge_index, edge_label_index, edge_label = select_properties(train_data)
    criterion = torch.nn.BCEWithLogitsLoss()

    out = model(x, edge_index, edge_label_index).view(-1)
    loss = criterion(out, edge_label)
    loss.backward()
    optimizer.step()
    return loss


# @torch.no_grad()
# def test(data: Union[HeteroData, Data], model: Module) -> float:
#     x, edge_index, edge_label_index, edge_label = select_properties(data)

#     model.eval()
#     out = model(x, edge_index, edge_label_index).view(-1)

#     return roc_auc_score(edge_label.cpu().numpy(), out.cpu().numpy())


@torch.no_grad()
def test(
    data: Union[HeteroData, Data], model: Module, exclude_edge_indices: list
) -> tuple[float, float]:

    x, edge_index_dict, edge_label_index, edge_label = select_properties(data)
    output = model.infer(x, edge_index_dict, edge_label_index)

    recall, precision, ndcg = get_metrics_universal(
        output, edge_index_dict, exclude_edge_indices, k=2
    )

    return recall, precision


def epoch_with_dataloader(
    model: Module,
    optimizer: Optimizer,
    train_loader: Union[LinkNeighborLoader, NeighborLoader],
    val_loader,
    test_loader,
    epoch_id: int,
):
    train_loop = tqdm(iter(train_loader))

    prof = profile.Profile()
    prof.enable()
    i = 0
    for data in train_loop:
        train_loop.set_description(f"Train, epoch: {epoch_id}")

        loss = train(data, model, optimizer)
        train_loop.set_postfix_str(f"Loss: {loss:.4f}")

    val_loop = tqdm(iter(val_loader))
    for data in val_loop:
        val_loop.set_description(f"Val, epoch: {epoch_id}")
        val_recall, val_precision = test(data, model, [])
        val_loop.set_postfix_str(f"Recall Val: {val_recall:.4f}")
        if i % 100 == 0:
            print("--------------")
            print("--------------")
            print("--------------")
            for aspect in ["cumtime", "ncalls", "tottime", "pcalls"]:
                print(f"------{aspect}--------")
                stats = pstats.Stats(prof).strip_dirs().sort_stats(aspect)
                stats.print_stats(15)  # top 10 rows

    test_loop = tqdm(iter(test_loader))
    for data in test_loop:
        val_loop.set_description(f"Test, epoch: {epoch_id}")
        test_recall, test_precision = test(data, model, [])
        test_loop.set_postfix_str(f"Recall Test: {test_recall:.4f}")
        i += 1

    # val_loop = tqdm(iter(val_loader))
    # for data in val_loop:
    #     val_recall, val_precision = test(data, model, [])
    #     val_loop.set_postfix_str(f"Recall Val: {val_recall:.4f}")

    # test_loop = tqdm(iter(test_loader))
    # for data in test_loop:
    #     test_recall, test_precision = test(data, model, [])
    #     test_loop.set_postfix_str(f"Recall Test: {test_recall:.4f}")

    # return loss, val_recall, test_recall, val_precision, test_precision

    return loss
