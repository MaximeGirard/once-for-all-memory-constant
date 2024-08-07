# Memory-constant OFA – A memory-optimized OFA architecture for tight memory constraints
#
# Implementation based on:
# Once for All: Train One Network and Specialize it for Efficient Deployment
# Han Cai, Chuang Gan, Tianzhe Wang, Zhekai Zhang, Song Han
# International Conference on Learning Representations (ICLR), 2020.

import argparse

import horovod.torch as hvd
import torch
import yaml

import wandb
from ofa.classification.run_manager.distributed_run_manager import \
    DistributedRunManager
from ofa.classification.run_manager.run_config import \
    DistributedImageNetRunConfig


# Function to load YAML configuration
def load_config(config_path):
    with open(config_path, "r") as file:
        return yaml.safe_load(file)
    
    
parser = argparse.ArgumentParser(description="OFA Training Script")
parser.add_argument("--config", required=True, help="Path to the configuration file")
args = parser.parse_args()

# Load configuration
config = load_config(args.config)

# Extract args from config
base_args = config["args"]
wandb_config = config["wandb"]

# Initialize Horovod
hvd.init()
torch.cuda.set_device(hvd.local_rank())

# Initialize wandb if enabled
if wandb_config["use_wandb"] and hvd.rank() == 0:
    wandb.init(project=wandb_config["project_name"], config=base_args, reinit=True)

print("Rank:", hvd.rank())

# Build run config
num_gpus = hvd.size()
print("Number of GPUs:", num_gpus)

# Print all visible GPU devices
print("Number of visible GPUs:", torch.cuda.device_count())
# Print their name
print("This process is running on :", torch.cuda.get_device_name(hvd.local_rank()))
# the others :
for i in range(torch.cuda.device_count()):
    if i != hvd.local_rank():
        print("Another GPU is available :", torch.cuda.get_device_name(i))

# Build run config
num_gpus = hvd.size()
print("Number of GPUs:", num_gpus)
base_args["init_lr"] = base_args["base_lr"] * num_gpus
base_args["train_batch_size"] = base_args["base_batch_size"]
base_args["test_batch_size"] = base_args["base_batch_size"] * 4
run_config = DistributedImageNetRunConfig(
    **base_args, num_replicas=num_gpus, rank=hvd.rank()
)

if base_args["model"] == "constant_V3":
    from ofa.classification.elastic_nn.networks import OFAMobileNetV3CtV3

    assert base_args["expand_list"] == [2, 3, 4]
    assert base_args["ks_list"] == [3, 5, 7]
    assert base_args["depth_list"] == [2, 3, 4]
    assert base_args["width_mult_list"] == 1.0
    model = OFAMobileNetV3CtV3
elif base_args["model"] == "constant_V2":
    from ofa.classification.elastic_nn.networks import OFAMobileNetV3CtV2

    assert base_args["expand_list"] == [1, 1.5, 2]
    assert base_args["ks_list"] == [3, 5, 7]
    assert base_args["depth_list"] == [2, 3, 4]
    assert base_args["width_mult_list"] == 1.0
    model = OFAMobileNetV3CtV2
elif base_args["model"] == "constant_V1":
    from ofa.classification.elastic_nn.networks import OFAMobileNetV3CtV1

    assert base_args["expand_list"] == [3, 4, 6]
    assert base_args["ks_list"] == [3, 5, 7]
    assert base_args["depth_list"] == [2, 3, 4]
    assert base_args["width_mult_list"] == 1.0
    model = OFAMobileNetV3CtV1
elif base_args["model"] == "MIT":
    from ofa.classification.elastic_nn.networks import OFAMobileNetV3

    assert base_args["expand_list"] == [3, 4, 6]
    assert base_args["ks_list"] == [3, 5, 7]
    assert base_args["depth_list"] == [2, 3, 4]
    assert base_args["width_mult_list"] == 1.0
    model = OFAMobileNetV3
else:
    raise NotImplementedError

net = model(
    n_classes=run_config.data_provider.n_classes,
    bn_param=(base_args["bn_momentum"], base_args["bn_eps"]),
    dropout_rate=base_args["dropout"],
    base_stage_width=base_args["base_stage_width"],
    width_mult=base_args["width_mult_list"],
    ks_list=base_args["ks_list"],
    expand_ratio_list=base_args["expand_list"],
    depth_list=base_args["depth_list"],
)

net.set_active_subnet(
    ks=max(base_args["ks_list"]),
    expand_ratio=max(base_args["expand_list"]),
    depth=max(base_args["depth_list"]),
)
teacher_net = net.get_active_subnet()

# Initialize DistributedRunManager
compression = hvd.Compression.fp16 if base_args["fp16_allreduce"] else hvd.Compression.none
run_manager = DistributedRunManager(
    base_args["t_path"],
    teacher_net,
    run_config,
    compression,
    backward_steps=base_args["dynamic_batch_size"],
    is_root=(hvd.rank() == 0),
)

run_manager.save_config()
run_manager.broadcast()
run_manager.load_model()

base_args["teacher_model"] = None

run_manager.train(
    base_args,
    warmup_epochs=base_args["warmup_epochs"],
    use_wandb=wandb_config["use_wandb"],
    wandb_tag="teacher",
)
run_manager.save_model()