from run_pipeline import run_pipeline
from config import link_pred_config

config = link_pred_config
config.epochs = 5
config.k = 30
config.eval_every = config.epochs
config.evaluate_break_at = 50
config.batch_size = 128
config.wandb_enabled = True

run_pipeline(config)
