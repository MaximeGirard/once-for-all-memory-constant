import os
import pickle

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import yaml
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, Dataset
from torch.optim.lr_scheduler import ReduceLROnPlateau

from ofa.nas.accuracy_predictor import AccuracyPredictor, MobileNetArchEncoder

# Assuming you have the AccuracyPredictor and MobileNetArchEncoder classes defined as in your previous code


class ArchDataset(Dataset):
    def __init__(self, features, accuracies):
        self.features = features
        self.accuracies = accuracies

    def __len__(self):
        return len(self.features)

    def __getitem__(self, idx):
        return self.features[idx], self.accuracies[idx]


def load_data(data_path):
    all_configs = []
    all_accuracies = []
    all_features = []

    for file in os.listdir(data_path):
        if file.endswith(".pkl"):
            with open(os.path.join(data_path, file), "rb") as f:
                data = pickle.load(f)
                all_configs.extend(data["configs"])
                all_accuracies.extend(data["accuracies"])
                all_features.extend(data["features"])

    return np.array(all_features), np.array(all_accuracies)


def train_predictor(arch_encoder, config, device="cuda"):
    # Load and prepare data
    features, accuracies = load_data(config["search_config"]["acc_dataset_path"])

    features, accuracies = features.astype(np.float32), accuracies.astype(np.float32)

    # Center the accuracies
    accuracy_mean = np.mean(accuracies)
    centered_accuracies = accuracies - accuracy_mean

    X_train, X_val, y_train, y_val = train_test_split(
        features,
        centered_accuracies,
        test_size=0.2,
        random_state=config["args"]["manual_seed"],
    )

    train_dataset = ArchDataset(X_train, y_train)
    val_dataset = ArchDataset(X_val, y_val)

    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True, num_workers=8)
    val_loader = DataLoader(val_dataset, batch_size=32, num_workers=8)

    # Initialize model
    model = AccuracyPredictor(arch_encoder, device=device).to(device)

    # Set base_acc to 0 initially (since we're working with centered accuracies)
    model.base_acc.data = torch.tensor([0.0], device=device)

    # Loss and optimizer
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=3e-2)

    # Learning rate scheduler
    scheduler = ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=5, verbose=True
    )

    # Training loop
    epochs = 50
    for epoch in range(epochs):
        model.train()
        train_loss = 0.0
        for features, accuracies in train_loader:
            features, accuracies = features.to(device), accuracies.to(device)

            optimizer.zero_grad()
            predictions = model(features)
            loss = criterion(predictions, accuracies)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()

        # Validation
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for features, accuracies in val_loader:
                features, accuracies = features.to(device), accuracies.to(device)
                predictions = model(features)
                val_loss += criterion(predictions, accuracies).item()

        train_loss /= len(train_loader)
        val_loss /= len(val_loader)

        print(
            f"Epoch {epoch+1}/{epochs}, Train Loss: {train_loss:.8f}, Val Loss: {val_loss:.8f}"
        )

        # Update learning rate
        scheduler.step(val_loss)

        # Print current learning rate
        current_lr = optimizer.param_groups[0]["lr"]
        print(f"Current learning rate: {current_lr:.6f}")

    print("Training completed.")

    # Set base_acc to the mean of the original accuracies
    model.base_acc.data = torch.tensor([accuracy_mean], device=device)
    
    # Evaluate on points from the validation set
    model.eval()
    with torch.no_grad():
        features, accuracies = next(iter(val_loader))
        features = features.to(device)
        predictions = model(features)
        print(f"Expected: {accuracies + accuracy_mean}, Predicted: {predictions}")
    
    return model


# Load configuration
def load_config(config_path):
    with open(config_path, "r") as file:
        return yaml.safe_load(file)


config = load_config("config_search_CompOFA.yaml")

# Initialize arch_encoder
arch_encoder = MobileNetArchEncoder(
    image_size_list=config["args"]["image_size"],
    depth_list=config["args"]["depth_list"],
    expand_list=config["args"]["expand_list"],
    ks_list=config["args"]["ks_list"],
    n_stage=5,
)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

trained_model = train_predictor(arch_encoder, config, device=device)

torch.save(trained_model.state_dict(), config["search_config"]["acc_predictor_checkpoint"])

print(f"Final base_acc: {trained_model.base_acc.item()}")




