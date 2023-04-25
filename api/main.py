from schematic import CONFIG
import os
from typing import Annotated
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import OAuth2PasswordBearer
from fastapi.security import HTTPBearer
from schematic.store.synapse import SynapseStorage

app = FastAPI()

os.environ["SCHEMATIC_CONFIG"]= "../config.yml"
token_auth_scheme = HTTPBearer()

def config_handler(asset_view=None):
    path_to_config = CONFIG.CONFIG_PATH
    
    # check if path to config is provided
    if os.path.isfile(path_to_config):
        CONFIG.load_config(path_to_config, asset_view = asset_view)

    else:
        raise FileNotFoundError(
            f"No configuration file was found at this path: {path_to_config}"
        )

# old storage/projects
@app.get("/storage/asset-views/{asset_view}/projects")
def get_storage_projects(asset_view: str, token: str = Depends(token_auth_scheme)):
    # call config handler 
    config_handler(asset_view=asset_view)

    # use Synapse storage 
    store = SynapseStorage(input_token=token.credentials)

    # call getStorageProjects function
    lst_storage_projects = store.getStorageProjects()
    
    return lst_storage_projects