"""
This module host loaders to safely download sources files and upload csv to database
"""
import gzip
import os
import requests
import certifi
import subprocess
from io import StringIO
from tempfile import NamedTemporaryFile

from botocore.client import Config
import boto3
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema

# Spaces connection parameters

endpoint_url = os.environ.get('SPACE_URL')
aws_access_key_id = os.environ.get('SPACE_ACCESS')
aws_secret_access_key = os.environ.get('SPACE_KEY')
space_bucket_name = os.environ.get('SPACE_BUCKET_NAME')


# Create a client for space

session = boto3.session.Session() 
CLIENT = session.client('s3', 
                        region_name='fr1', 
                        endpoint_url=endpoint_url,
                        aws_access_key_id=aws_access_key_id, 
                        aws_secret_access_key=aws_secret_access_key,
                        config=Config(signature_version='s3v4'))


# Database connection parameters
user = os.getenv('POSTGRES_USER')
password = os.getenv('POSTGRES_PASSWORD')
host = os.getenv('POSTGRES_HOST')
port = os.getenv('POSTGRES_PORT')
database = os.getenv('POSTGRES_DB')


extract_schema_name = 'sources'

# Create a connection to the database
ENGINE = create_engine(f'postgresql://{user}:{password}@{host}:{port}/{database}')

# Create schema if don't exist
with ENGINE.connect() as connection:
    connection.execute(CreateSchema(extract_schema_name, if_not_exists=True))
    connection.commit()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
}

def read_from_source(path, rows_to_skip=None):
    """
    Wrapper around pandas.read_csv and pandas.read_json to safely download the file from the given path without raisin ssl errors
    """
    # Download the file
    response = requests.get(path, headers=HEADERS, verify=certifi.where())

    # Check the file extension
    if path.endswith('.csv'):
        # Load the data from the downloaded CSV file
        data = pd.read_csv(StringIO(response.text), skiprows=rows_to_skip)
    elif path.endswith('.gz'):
        # Decompress the gzipped data
        decompressed_file = gzip.decompress(response.content)
        # Load the data from the decompressed file
        data = pd.read_csv(StringIO(decompressed_file.decode('utf-8')), low_memory=False, skiprows=rows_to_skip)
    elif path.endswith('.json'):
        # Load the data from the downloaded JSON file
        data = pd.read_json(StringIO(response.text))
    else:
        raise ValueError(f"Unsupported file extension in path: {path}")

    return data


def upload_dataframe_to_table(data, table_name):
    """
    Upload a dataframe to a table in the database
    """
    try:
        data.to_sql(table_name, ENGINE, if_exists='replace', index=False, schema=extract_schema_name)
    except Exception as e:
        print(e)
        connection = ENGINE.connect()
        connection.rollback()
        connection.close()
        raise

def upload_dataframe_to_storage(df, filename):
    """
    Upload a DataFrame to S3-compatible storage using s3cmd.
    """
    # Create a temporary file
    with NamedTemporaryFile(suffix='.csv', delete=False) as tmpfile:
        # Write the DataFrame to the temporary file
        df.to_csv(tmpfile.name, index=False)

        # Upload the DataFrame to storage using s3cmd
        subprocess.run([
            "s3cmd", 
            "put", 
            tmpfile.name, 
            f"s3://{space_bucket_name}/{filename}",
            "--access_key", aws_access_key_id,
            "--secret_key", aws_secret_access_key,
            "--host", endpoint_url,
            "--host-bucket", space_bucket_name,
            "--no-check-certificate"
        ], check=True)

def read_from_storage(filename):
    """
    Read a file from S3-compatible storage into a DataFrame.
    """
    # Get the file object
    obj = CLIENT.get_object(Bucket=space_bucket_name, Key=filename)

    # Read the file into a DataFrame
    df = pd.read_csv(obj['Body'])

    return df