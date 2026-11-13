import pandas as pd

# Load NASA VIIRS dataset
df = pd.read_csv("viirs.csv")

# 1. Display dataset dimensions
print("Dataset Shape:")
print(df.shape)

# 2. Display column names
print("\nColumns:")
print(df.columns.tolist())

# 3. Display first 5 records
print("\nFirst 5 Records:")
print(df.head())

# 4. Display dataset information
print("\nDataset Information:")
df.info()

# 5. Check missing values
print("\nMissing Values:")
print(df.isnull().sum())
