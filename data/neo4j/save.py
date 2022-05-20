import pandas as pd
import os
import time
from utils.pandas import drop_columns_if_exist


def save_to_csv(dataframe: pd.DataFrame, name: str):
    dataframe.to_csv(f"data/saved/{name}.csv", index=False)


def save_to_neo4j(
    customers: pd.DataFrame, articles: pd.DataFrame, transactions: pd.DataFrame
):
    print("| Saving to neo4j...")
    print("| Processing customer nodes...")
    customers = customers.copy()
    customers[":LABEL"] = "Customer"
    customers.rename(columns={"index": ":ID(Customer)"}, inplace=True)
    customers["_id"] = customers[":ID(Customer)"]
    save_to_csv(customers, "customers")

    print("| Processing article nodes...")
    articles = articles.copy()
    articles[":LABEL"] = "Article"
    articles.rename(columns={"index": ":ID(Article)"}, inplace=True)
    articles["_id"] = articles[":ID(Article)"]
    save_to_csv(articles, "articles")

    print("| Renaming transactions...")
    transactions = transactions.copy()
    transactions.rename(
        columns={
            "customer_id": ":START_ID(Customer)",
            "article_id": ":END_ID(Article)",
        },
        inplace=True,
    )

    transactions = drop_columns_if_exist(
        transactions, ["t_dat", "price", "sales_channel_id", "year-month"]
    )

    transactions["train_mask"] = transactions["train_mask"].astype(int)
    transactions["test_mask"] = transactions["test_mask"].astype(int)
    transactions["val_mask"] = transactions["val_mask"].astype(int)

    print("| Changing the edge names...")
    transactions[":TYPE"] = transactions.apply(
        lambda x: "BUYS_TRAIN"
        if x["train_mask"] == 1
        else "BUYS_VAL"
        if x["val_mask"] == 1
        else "BUYS_TEST",
        axis=1,
    )

    save_to_csv(transactions, "transactions")
    # Neo4j needs to be stopped for neo4j-admin import to run
    print("| Stopping running instances of Neo4j...")
    os.system("neo4j stop")
    print("| Importing csv to database...")
    os.system(
        "neo4j-admin import --database=neo4j --nodes=data/saved/articles.csv --nodes=data/saved/customers.csv --relationships=data/saved/transactions.csv --force"
    )
    print("| Starting Neo4j...")
    os.system("neo4j start")
    time.sleep(8)
    # Create the indexes for Customer & Article node types
    print("| Creating indexes...")

    os.system(
        "echo 'CREATE INDEX ON :Customer(ID)' | cypher-shell -u neo4j -p password --format plain"
    )
    os.system(
        "echo 'CREATE INDEX ON :Customer(_id)' | cypher-shell -u neo4j -p password --format plain"
    )
    os.system(
        "echo 'CREATE INDEX ON :Article(ID)' | cypher-shell -u neo4j -p password --format plain"
    )
    os.system(
        "echo 'CREATE INDEX ON :Article(_id)' | cypher-shell -u neo4j -p password --format plain"
    )
    os.system(
        "echo 'CREATE FULLTEXT INDEX relationship_index FOR ()-[r:BUYS]-() ON EACH [r.train_mask]' | cypher-shell -u neo4j -p password --format plain"
    )

    print("Number of nodes in the database:")
    os.system(
        "echo 'MATCH (n) RETURN count(n)' | cypher-shell -u neo4j -p password --format plain"
    )