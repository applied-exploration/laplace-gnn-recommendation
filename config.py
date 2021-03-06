from dataclasses import dataclass
from typing import Optional, Union, Tuple
from data.types import (
    PreprocessingConfig,
    UserColumn,
    ArticleColumn,
    DataType,
)
from utils.profiling import Profiler
from utils.constants import Constants

embedding_range_dict = {
    "2": 2,
    "10": 4,
    "1000": 12,
    "10000": 20,
    "100000": 40,
    "1000000": 60,
}


@dataclass
class Config:
    wandb_enabled: bool
    epochs: int  # number of training epochs
    hidden_layer_size: int
    encoder_layer_output_size: int  # Context vector size
    k: int  # value of k for recall@k. It is important to set this to a reasonable value!
    num_gnn_layers: int  # number of  layers (i.e., number of hops to consider during propagation)
    num_linear_layers: int  # number of linear layers in the decoder
    learning_rate: float
    conv_agg_type: str  # "add", "mean", "max", "lstm"
    heterogeneous_prop_agg_type: str  # "sum", "mean", "min", "max", "mul"
    save_model: bool
    eval_every: int  # evaluation to run every n epoch
    save_every: float  # How often the model should be saved, Ratio of epochs (eg.: 0.2 * epoch_num)

    batch_size: int  # batch size. refers to the # of customers in the batch (each will come with all of its edges)
    num_neighbors: int  # sample n neighbors for each node for n_hop_neighbors iterations
    n_hop_neighbors: int
    num_workers: int  # number of workers to use for data loading
    candidate_pool_size: int  # How many precalculated candidates we should give over
    positive_edges_ratio: float  # Ratio of positive edges that we sample for edge_label_index, eg.: 0.5 means we take the half of the avilable edges from that user, the result won't be less than 1 (We will always sample at least one positive edge)
    negative_edges_ratio: float  # How many negative edges to sample based on the positive ones, eg.: 10 means we take 10*sampled_positive_edges
    batch_norm: bool
    matchers: str  # "fashion" "movielens"

    p_dropout_edges: Optional[float]  # dropout probability for edges
    p_dropout_features: Optional[float]  # dropout probability for nodes

    default_edge_types: list[Tuple[str, str, str]]
    other_edge_types: list[Tuple[str, str, str]]
    node_types: list[str]

    profiler: Optional[Profiler] = None
    evaluate_break_at: Optional[
        int
    ] = None  # Eval and Test should break after this many iterations (not epochs!) None runs whole test and val
    neo4j: bool = False  # Should the dataset use neo4j database or not

    def print(self):
        print("\nConfiguration is:")
        for key, value in vars(self).items():
            print(f"{key:>20}: {value}")
        print("\x1b[0m")

    def check_validity(self):
        assert (
            self.positive_edges_ratio <= 1.0
        ), "Positive Edges ratio has to be smaller than 1.0"
        assert self.p_dropout_edges <= 1.0, "p_dropout_edges cannot be bigger than 1.0"
        assert (
            self.p_dropout_features <= 1.0
        ), "p_dropout_features cannot be bigger than 1.0"


@dataclass
class LightGCNConfig:
    epochs: int  # number of training epochs
    hidden_layer_size: int
    k: int  # value of k for recall@k. It is important to set this to a reasonable value!
    learning_rate: float
    save_model: bool
    eval_every: int  # (LightGCN) evaluation to run every n epoch
    lr_decay_every: int  # (LightGCN) lr decay to run every n epoch
    Lambda: float  # (LightGCN)
    batch_size: int  # batch size. refers to the # of customers in the batch (each will come with all of its edges)
    num_iterations: int
    show_graph: bool
    num_recommendations: int

    def print(self):
        print("\nConfiguration is:")
        for key, value in vars(self).items():
            print(f"{key:>20}: {value}")
        print("\x1b[0m")


link_pred_config = Config(
    matchers="movielens",  # "fashion" or "movielens"
    wandb_enabled=False,
    epochs=4,
    k=12,
    num_gnn_layers=2,
    num_linear_layers=2,
    hidden_layer_size=128,
    encoder_layer_output_size=64,
    conv_agg_type="add",
    heterogeneous_prop_agg_type="sum",
    learning_rate=0.01,
    save_model=False,
    batch_size=24,  # combination of batch_size with num_neighbors and n_hop_neighbors and num_workers determines if data would fit on gpu
    num_neighbors=64,  #
    n_hop_neighbors=3,
    num_workers=1,
    candidate_pool_size=20,
    positive_edges_ratio=0.5,
    negative_edges_ratio=3.0,
    eval_every=1,
    save_every=0.2,  #
    profiler=None,  # Profiler(every=20),
    evaluate_break_at=None,
    p_dropout_edges=0.2,  # Currently not being used!
    p_dropout_features=0.3,
    batch_norm=True,
    neo4j=True,
    default_edge_types=[Constants.edge_key],
    # other_edge_types=[Constants.edge_key_extra],
    other_edge_types=[],
    node_types=[
        Constants.node_user,
        Constants.node_item,
        # Constants.node_extra,
    ],
)


lightgcn_config = LightGCNConfig(
    epochs=10000,
    k=12,
    hidden_layer_size=32,
    learning_rate=1e-3,
    save_model=False,
    batch_size=128,
    num_iterations=4,
    eval_every=100,
    lr_decay_every=100,
    Lambda=1e-6,
    show_graph=False,
    num_recommendations=256,
)


preprocessing_config = PreprocessingConfig(
    customer_features=[
        UserColumn.PostalCode,
        UserColumn.FN,
        UserColumn.Age,
        UserColumn.ClubMemberStatus,
        UserColumn.FashionNewsFrequency,
        UserColumn.Active,
    ],
    article_features=[
        ArticleColumn.ProductCode,
        ArticleColumn.ProductTypeNo,
        ArticleColumn.GraphicalAppearanceNo,
        ArticleColumn.ColourGroupCode,
    ],
    article_non_categorical_features=[ArticleColumn.ImgEmbedding],
    filter_out_unconnected_nodes=True,
    load_image_embedding=False,
    load_text_embedding=False,
    text_embedding_colname="derived_look",
    data_size=10_000,
    save_to_neo4j=True,
    data_type=DataType.pyg,
)
