# LinkProp and LinkProp-Multi from the paper:
# Revisiting Neighborhood-based Link Prediction for Collaborative Filtering
# https://arxiv.org/abs/2203.15789

import numpy as np
import torch
from torch_sparse import SparseTensor, matmul
import pandas as pd
from tqdm import tqdm
from sklearn.metrics import ndcg_score, recall_score, precision_score, accuracy_score
from sklearn.model_selection import GridSearchCV
from sklearn.base import BaseEstimator


def load_data():
    try:
        return torch.load("data/derived/link_prop.pt")
    except FileNotFoundError:
        pass

    customers = pd.read_csv("data/original/customers.csv")
    articles = pd.read_csv("data/original/articles.csv")
    transactions = pd.read_csv("data/original/transactions_train.csv")
    customers.reset_index()
    articles.reset_index()

    src = list(customers["customer_id"])
    src_map = {v: k for k, v in enumerate(src)}
    dest = list(articles["article_id"])
    dest_map = {v: k for k, v in enumerate(dest)}

    transactions.reset_index()
    transactions["src"] = transactions["customer_id"].map(src_map)
    transactions["dest"] = transactions["article_id"].map(dest_map)

    # shuffle transactions
    edge_index = torch.tensor(
        transactions[["src", "dest"]].sample(frac=1).values, dtype=torch.long
    ).T

    # create user-item interaction matrix
    M = SparseTensor(
        row=edge_index[0],
        col=edge_index[1],
        value=torch.ones(len(transactions), dtype=torch.float),
        sparse_sizes=(len(src), len(dest)),
    ).coalesce("max")

    torch.save((M, src, dest, src_map, dest_map), "data/derived/link_prop.pt")

    return M, src, dest, src_map, dest_map


def split_data(start, count, M):
    """Selects a range of users"""
    return M[start : start + count, :].coalesce("max")  # .to_dense()


def sparse_assign(m, i, j, val):
    """Sets a value in a sparse matrix at (i,j)"""
    assert i < m.size(0) and j < m.size(1), "can only assign to existing indices"
    row, col, value = m.coo()
    prev = m[i.item(), j.item()].to_dense().squeeze()
    return SparseTensor(
        row=torch.cat((row, torch.tensor([i], dtype=torch.long))),
        col=torch.cat((col, torch.tensor([j], dtype=torch.long))),
        value=torch.cat((value, torch.tensor([val], dtype=torch.float) - prev)),
        sparse_sizes=(m.size(0), m.size(1)),
    ).coalesce("add")


def sparse_fill_on_mask(m, mask, val, dim=0):
    """Fills masked values in a sparse matrix with a row or col mask. Only sets values that have been explicitly set before."""
    row, col, value = m.coo()
    prev_row, prev_col, prev_value = m[mask, :].coo() if dim == 0 else m[:, mask].coo()
    # restore previous values' indices, assumes mask is boolean and same length as m
    if dim == 0:
        prev_row = torch.arange(len(mask))[mask][prev_row]
    else:
        prev_col = torch.arange(len(mask))[mask][prev_col]
    return SparseTensor(
        row=torch.cat((row, prev_row)),
        col=torch.cat((col, prev_col)),
        value=torch.cat((value, val - prev_value)),
        sparse_sizes=(m.size(0), m.size(1)),
    ).coalesce("add")


def sparse_cat(m, n):
    """Stack two sparse matrices"""
    assert m.size(1) == n.size(
        1
    ), "cannot stack matrices with different number of columns"
    row, col, value = n.coo()
    row = row + m.size(0)
    m_row, m_col, m_value = m.coo()
    return SparseTensor(
        row=torch.cat((m_row, row)),
        col=torch.cat((m_col, col)),
        value=torch.cat((m_value, value)),
        sparse_sizes=(m.size(0) + n.size(0), m.size(1)),
    ).coalesce("add")


def sparse_batch_op(batch_size, op, m, n):
    """Batch runs any torch or numpy operation on 2 sparse matrices with same dims"""
    result = SparseTensor(
        row=torch.tensor([], dtype=torch.long),
        col=torch.tensor([], dtype=torch.long),
        value=torch.tensor([], dtype=torch.float),
        sparse_sizes=(0, m.size(1)),
    )
    for i in range(0, m.size(0), batch_size):
        end = min(i + batch_size, m.size(0))
        res = SparseTensor.from_dense(
            op(
                m[i:end, :].to_dense(),
                n[i:end, :].to_dense(),
            )
        )
        result = sparse_cat(result, res)
    return result


def sparse_nonzero(m):
    """Returns the nonzero indices of a sparse matrix"""
    row, col, value = m.clone().coo()
    return row[value != 0], col[value != 0], value[value != 0]


def intersect2d(a, b):
    """Returns the intersection of two 2D arrays row by row"""
    return np.array([np.intersect1d(a[i], b[i]) for i in range(len(a))])


def sample_user_items(target, ratio=0.4):
    """Drop some edges for users that have more than 1 item"""
    user_deg = target.sum(dim=1)
    candidate = sparse_fill_on_mask(target, user_deg > 1, 2, dim=0)
    row, col, value = sparse_nonzero(candidate)
    rand_mask = (value == 2) & (
        torch.rand(len(value)) < ((value == 2).sum() / len(value) * ratio)
    )
    value[rand_mask] = 0
    value[value == 2] = 1
    data = SparseTensor(
        row=row,
        col=col,
        value=value,
        sparse_sizes=(target.size(0), target.size(1)),
    ).coalesce("add")

    # take observed out of ground truth
    # TODO does this create users with 0 items?
    row, col, value = data.clone().coo()
    value[value == 1] = 2
    value[value == 0] = 1
    value[value == 2] = 0
    target_new_links = SparseTensor(
        row=row,
        col=col,
        value=value,
        sparse_sizes=(target.size(0), target.size(1)),
    ).coalesce("add")

    return data, target_new_links


def mean_average_precision(y_true, y_pred, k=12):
    """Courtesy of https://www.kaggle.com/code/george86/calculate-map-12-fast-faster-fastest"""
    # compute the Rel@K for all items
    rel_at_k = np.zeros((len(y_true), k), dtype=int)

    # collect the intersection indexes (for the ranking vector) for all pairs
    for idx, (truth, pred) in enumerate(zip(y_true, y_pred)):
        _, _, inter_idxs = np.intersect1d(
            truth, pred[:k], assume_unique=True, return_indices=True
        )
        rel_at_k[idx, inter_idxs] = 1

    # Calculate the intersection counts for all pairs
    intersection_count_at_k = rel_at_k.cumsum(axis=1)

    # we have the same denominator for all ranking vectors
    ranks = np.arange(1, k + 1, 1)

    # Calculating the Precision@K for all Ks for all pairs
    precisions_at_k = intersection_count_at_k / ranks
    # Multiply with the Rel@K for all pairs
    precisions_at_k = precisions_at_k * rel_at_k

    # Calculate the average precisions @ K for all pairs
    average_precisions_at_k = precisions_at_k.mean(axis=1)

    # calculate the final MAP@K
    map_at_k = average_precisions_at_k.mean()

    return map_at_k


class LinkPropMulti(BaseEstimator):
    def __init__(self, rounds, k, alpha, beta, gamma, delta):
        super().__init__()
        self.rounds, self.k, self.alpha, self.beta, self.gamma, self.delta = (
            rounds,
            k,
            alpha,
            beta,
            gamma,
            delta,
        )
        self.user_degrees = None
        self.item_degrees = None
        self.M = None
        self.M_alpha_beta = None
        self.M_gamma_delta = None

    def fit(self, X):
        return self

    def fit_for_score(self, M):
        # reset model
        self.M_alpha_beta = None
        self.M_gamma_delta = None

        # get node degrees
        self.user_degrees = M.sum(dim=1)
        self.item_degrees = M.sum(dim=0)

        # exponentiate degrees by model params
        user_alpha = self.user_degrees ** (-self.alpha)
        item_beta = self.item_degrees ** (-self.beta)
        user_gamma = self.user_degrees ** (-self.gamma)
        item_delta = self.item_degrees ** (-self.delta)

        # get rid of inf from 1/0
        user_alpha[torch.isinf(user_alpha)] = 0.0
        item_beta[torch.isinf(item_beta)] = 0.0
        user_gamma[torch.isinf(user_gamma)] = 0.0
        item_delta[torch.isinf(item_delta)] = 0.0

        # to keepe sparsity we can calculate the outer product only for the edges
        # so instead of, outer products: alpha_beta = user_alpha.reshape((-1, 1)) * item_beta
        # and hadamard product: M_alpha_beta = M * alpha_beta
        # we can do:
        row, col, value = sparse_nonzero(M)
        self.M_alpha_beta = SparseTensor(
            row=row,
            col=col,
            value=user_alpha[row] * item_beta[col] * value,
            sparse_sizes=(M.size(0), M.size(1)),
        )
        self.M_gamma_delta = SparseTensor(
            row=row,
            col=col,
            value=user_gamma[row] * item_delta[col] * value,
            sparse_sizes=(M.size(0), M.size(1)),
        )

        # (lazy) link propagation, call get_preds_for_users

        # get top k new links
        # TODO iterate over users
        # target_pred = self.predict_k(M, self.k)

        # # with the new links recalculate and store node degrees for next round
        # M_new = (M.clone() + target_pred).clamp(max=1)
        # self.user_degrees = M_new.sum(dim=1)
        # self.item_degrees = M_new.sum(dim=0)

    def get_preds_for_users(self, start, end):
        return matmul(matmul(self.M_alpha_beta[start:end], M.t()), self.M_gamma_delta)

    def predict_k(self, M, start, end, k):
        """Return top k new links"""
        user_pred = self.get_preds_for_users(start, end).to_dense()
        # take observed links out of predictions
        user_pred = (user_pred - (M[start:end].to_dense() == 1).float() * 100000).clamp(
            min=0
        )
        return user_pred.topk(k, dim=1)[1]

    def predict(self, users_ix, k):
        # return self.L[users_ix, :].topk(k, dim=1)[1]
        pass

    # def score(self, X, y):
    #     return ndcg_score(true_target, target_pred)

    def score_mapk(self, X, y, batch_size=400):
        self.fit_for_score(X)
        user_topk = torch.empty(size=(0, self.k))
        target_topk = torch.empty(size=(0, self.k))
        scores = []
        for start in tqdm(range(0, X.size(0), batch_size)):
            end = min(start + batch_size, X.size(0))
            user_pred = self.predict_k(X, start, end, self.k)
            target_pred = y[start:end].to_dense().squeeze().topk(self.k, dim=1)[1]
            user_topk = torch.cat((user_topk, user_pred))
            target_topk = torch.cat((target_topk, target_pred))
            scores.append(mean_average_precision(target_topk, user_topk, k=self.k))
            print(np.array(scores).mean())

        # TODO only calculating for users with at least one item in true target
        # target_t = target_topk[(y[start:end].to_dense().squeeze().topk(self.k, dim=1)[0] > 0).any(dim=1)]
        return np.array(scores).mean(), user_topk


M, src, dest, src_map, dest_map = load_data()
data, target = sample_user_items(M, 0.4)
linkProp = LinkPropMulti(rounds=1, k=12, alpha=0.1, beta=0.1, gamma=0.2, delta=0.5)
score, preds = linkProp.score_mapk(data, target)
submission = pd.DataFrame(preds.apply_(lambda x: dest[x])).astype("string")
submission["prediction"] = (
    submission[[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]]
    .agg(" 0".join, axis=1)
    .apply(lambda x: "0" + x)
)
submission["customer_id"] = src[: len(submission)]
submission[["customer_id", "prediction"]].to_csv(
    "data/derived/submission_3.csv", index=False
)

# TODO find optimal params
# param_grid = [
#     {
#         "alpha": [0.0, 0.5],
#         "beta": [0.0, 0.5],
#         "gamma": [0.0, 0.5],
#         "delta": [0.0, 0.5],
#         "k": [3],
#         "rounds": [1],
#     },
# ]
# linkProp = LinkPropMulti(rounds=1, k=12, alpha=0, beta=0, gamma=0, delta=0)
# clf = GridSearchCV(
#     linkProp,
#     param_grid=param_grid,
#     verbose=2,
#     return_train_score=True,
# )
# clf.fit(data, target)
# sorted(clf.cv_results_.keys())
# print(clf.cv_results_)
