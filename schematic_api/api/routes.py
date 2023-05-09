from __future__ import annotations
import logging
import os
import pickle
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass

import connexion
import pandas as pd
from flask import current_app as app
from flask import send_from_directory
from flask_cors import cross_origin
from typing import Union, BinaryIO, List, Optional
from pydantic import BaseModel

from schematic import CONFIG
from schematic.manifest.generator import ManifestGenerator
from schematic.models.metadata import MetadataModel
from schematic.schemas.explorer import SchemaExplorer
from schematic.schemas.generator import SchemaGenerator
from schematic.store.synapse import ManifestDownload, SynapseStorage
from schematic.utils.general import entity_type_mapping
from schematic.visualization.attributes_explorer import AttributesExplorer
from schematic.visualization.tangled_tree import TangledTree

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

def config_handler(asset_view: str=None):
    """Load schematic config 

    Args:
        asset_view (str, optional): ID of a file view. A file view lists all files or tables within one or more folders or projects. If not provided, use master_fileview from config.yml

    Raises:
        FileNotFoundError: no config.yml found
    """
    path_to_config = app.config["SCHEMATIC_CONFIG"]
    
    # check if path to config is provided
    if os.path.isfile(path_to_config):
        CONFIG.load_config(path_to_config, asset_view = asset_view)

    else:
        raise FileNotFoundError(
            f"No configuration file was found at this path: {path_to_config}"
        )

class JsonConverter:
    '''
    Mainly handle converting json str or json file to csv
    '''
    def readJson(self, json_str=None, manifest_file=None):
        '''
        The purpose of this function is to read either json str or json file
        input: 
            json_str: json object
            manifest_file: manifest file object 
        output: 
            return a dataframe
        '''
        if json_str:
            df = pd.read_json(json_str)
        elif manifest_file: 
            df = pd.read_json(manifest_file.read())
        return df
    
    def get_file(self, file_key):
        '''
        The purpose of this function is to get the file uploaded by user
        input: 
            file_key: Defined in api.yaml. This key refers to the files uploaded. 
            manifest_file: manifest file object 
        output: 
            return file object
        '''

        manifest_file = connexion.request.files[file_key]
        return manifest_file

    def IsJsonFile(self, manifest_file):
        '''
        The purpose of this function is check if the manifest file that gets uploaded is a json or not
        input: 
            manifest_file: manifest file object 
        output: 
            return True if it is json
        '''

        file_type = manifest_file.content_type
        if file_type == 'application/json':
            return True
        else: 
            return False

    def convert_df_to_csv(self, df, file_name):
        '''
        The purpose of this function is to convert dataframe to a temporary CSV file
        input: 
            df: dataframe
            file_name: file name of the output csv
        output: 
            return temporary file path of the output csv
        '''

        # convert dataframe to a temporary csv file
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, file_name)
        df.to_csv(temp_path, encoding = 'utf-8', index=False)
        return temp_path

    def convert_json_str_to_csv(self, json_str, file_name):
        '''
        The purpose of this function is to convert json str to a temporary csv file
        input: 
            json_str: json object
            file_name: file name of the output csv
        output: 
            return temporary file path of the output csv
        '''

        # convert json to df
        df = self.readJson(json_str = json_str)

        # convert dataframe to a temporary csv file
        temp_path = self.convert_df_to_csv(df, file_name)

        return temp_path

    def convert_json_file_to_csv(self, file_key):
        '''
        The purpose of this function is to convert json str to a temporary csv file
        input: 
            file_key: Defined in api.yaml. This key refers to the files uploaded. 
        output: 
            return temporary file path of the output csv
        '''

        # get manifest file
        manifest_file = self.get_file(file_key)

        if self.IsJsonFile(manifest_file):
            # read json as dataframe
            df = self.readJson(manifest_file = manifest_file)
            # get base file name
            base = os.path.splitext(manifest_file.filename)[0]
            # name the new csv file 
            new_file_name = base + '.csv'
            # convert to csv
            temp_path = self.convert_df_to_csv(df, new_file_name)
            return temp_path
        else: 
            temp_path = save_file(file_key='file_name')
            return temp_path
        
def parse_bool(str_bool):
    if str_bool.lower().startswith('t'):
        return True
    elif str_bool.lower().startswith('f'):
        return False
    else:
        raise ValueError(
            "String boolean does not appear to be true or false. Please verify input."
        )

def return_as_json(manifest_local_file_path):
    manifest_csv = pd.read_csv(manifest_local_file_path)
    manifest_json = manifest_csv.to_dict(orient="records")
    return manifest_json

def save_file(file_key="csv_file"):
    '''
    input: 
        file_key: Defined in api.yaml. This key refers to the files uploaded. By default, set to "csv_file"
    Return a temporary file path for the uploaded a given file
    '''
    manifest_file = connexion.request.files[file_key]

    # save contents of incoming manifest CSV file to temp file
    temp_dir = tempfile.gettempdir()
    # path to temp file where manifest file contents will be saved
    temp_path = os.path.join(temp_dir, manifest_file.filename)
    # save content
    manifest_file.save(temp_path)

    return temp_path

def initalize_metadata_model(schema_url):
    jsonld = get_temp_jsonld(schema_url)
    metadata_model = MetadataModel(
        inputMModelLocation=jsonld, inputMModelLocationType="local"
    )
    return metadata_model

def get_temp_jsonld(schema_url):
    # retrieve a JSON-LD via URL and store it in a temporary location
    with urllib.request.urlopen(schema_url) as response:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".model.jsonld") as tmp_file:
            shutil.copyfileobj(response, tmp_file)

    # get path to temporary JSON-LD file
    return tmp_file.name

class ManifestGeneration(BaseModel):
    schema_url: str
    access_token: Optional[str] = ''
    asset_view: Optional[str] = ''
    dataset_id: List[str] = []
    data_type: List[str] = []
    title: Optional[str] = ''
    output_format: Optional[str] = "excel"
    use_annotations: Optional[bool] = False
    
    def _load_config_(self) -> None:
        """load configuration file and update asset view if needed
        """
        config_handler(asset_view = self.asset_view)

    def _check_dataset_match_datatype_(self) -> None:
        """check if number of dataset ids matches number of data types

        Raises:
            ValueError: mismatch in the number of data_types and dataset_ids
        """
        # the number of dataset_ids (if applicable)
        len_data_types = len(self.data_type)
        len_dataset_ids = len(self.dataset_id)
        
        try:
            len_data_types == len_dataset_ids
        except:
            raise ValueError(
                    f"There is a mismatch in the number of data_types and dataset_id's that "
                    f"submitted. Please check your submission and try again."
                )

    def _get_manifest_title(self, single_data_type:str='') -> str:
        """get title of manifest

        Args:
            single_data_type (str): data type of manifest. Defaults to None.

        Returns: 
            str: title of manifest
        """
        if self.title:
            t = f'{self.title}.{single_data_type}.manifest'
        else: 
            t = f'Example.{single_data_type}.manifest'         
        return t

    def generate_manifest_and_collect_outputs(self, data_type_lst: List[str], dataset_id: List[str]=[]):
        """_summary_

        Args:
            data_type_lst (List[str]): _description_.
            dataset_id (List[str], optional): _description_. Defaults to [].

        Returns:
            _type_: _description_
        """
        # if requested output is excel, only use the first data type or first dataset id (if provided) to generate a manifest
        if self.output_format == "excel":
            # get manifest title based on data type
            t = self._get_manifest_title(single_data_type=data_type_lst[0])
            if len(data_type_lst) > 1:
                # warn users that only the first manifest gets returned
                app.logger.warning(f'Currently we do not support returning multiple files as Excel format at once. Only {t} would get returned. ')
            # if multiple dataset ids are provided, use the first one
            if dataset_id:
                app.logger.warning(f'Currently we do not support returning multiple files as Excel format at once. Only manifest generated by using dataset id {dataset_id[0]} would get returned with title {t}')
                return self.create_single_manifest(single_data_type=data_type_lst[0], title=t, single_dataset_id=dataset_id[0])
            else:
                return self.create_single_manifest(single_data_type=data_type_lst[0], title=t)

        else:
            # if output format is google sheet or data frame, simply create outputs and append all outputs to a list
            all_outputs = []
            for i, dt in enumerate(data_type_lst):
                t = self._get_manifest_title(single_data_type=dt)
                if dataset_id:
                    # get existing manifest
                    # here we could assume that dataset_id list and data_type list have the same length since _check_dataset_match_datatype_ has done the check
                    output = self.create_single_manifest(single_data_type=dt, single_dataset_id=dataset_id[i], title=t)
                else:
                    # get new manifests in google sheet or data frame format
                    output = self.create_single_manifest(single_data_type=dt, title=t)
                # collect outputs in a list
                all_outputs.append(output)
            
            return all_outputs
                

    def create_single_manifest(self, single_data_type:str='', single_dataset_id:str='', title:str='') -> str|pd.DataFrame|BinaryIO:
        """call get_manifest generate function to generate a new manifest

        Args:
            single_data_type (str, optional): data type of a manifest being generated. Defaults to ''.
            single_dataset_id (str, optional): dataset id of an existing manifest. Defaults to ''.
            title (str, optional): title of new manifest. Defaults to ''.

        Returns:
            str|pd.DataFrame|BinaryIO: depends on output_format parameter, returns either a google sheet url, dataframe, or an excel file in attachment
        """

        manifest_generator = ManifestGenerator(
            path_to_json_ld=self.schema_url,
            title=title,
            root=single_data_type,
            use_annotations=self.use_annotations
        )

        result = manifest_generator.get_manifest(
                dataset_id=single_dataset_id, output_format=self.output_format, access_token=self.access_token
        )

        # return an excel file if output_format is set to "excel"
        if self.output_format == "excel":
            dir_name = os.path.dirname(result)
            file_name = os.path.basename(result)
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            return send_from_directory(directory=dir_name, path=file_name, as_attachment=True, mimetype=mimetype, max_age=0)
                
        return result

    @staticmethod
    def get_manifests_route(schema_url: str, output_format: str, access_token: str='', use_annotations: bool=False, dataset_id: list[str]=[], data_type:list[str] = [], asset_view: str = None, title: str = '') -> str|pd.DataFrame|BinaryIO:
        """Generate a new manifest template or create an existing manifest in google sheet/excel/dataframe format. 

        Args:
            schema_url (str): data model in json ld format.
            output_format (str): output format of manifests. Available options are: google sheet, excel, and dataframe
            access_token (str): access token
            title (str, optional): title of the new manifest. Defaults to ''
            use_annotations (bool, optional): whether to use existing annotations during manifest generation. Defaults to False
            dataset_id (list, optional): id of a given dataset. Defaults to an empty list
            asset_view (str, optional): id of a file view. A file view lists all files or tables within one or more folders or projects. Defaults to use master_fileview in config.yml
            data_type (list, optional): a list of data types. Defaults to an empty list

        Raises:
            ValueError: There is a mismatch in the number of data_types and dataset_id's that submitted. Please check your submission and try again.
            ValueError: When submitting 'all manifests' as the data_type cannot also submit dataset_id. Please check your submission and try again.

        Returns:
            str|pd.DataFrame|BinaryIO: returns google sheet url, pandas dataframe, or excel file based on output_format parameter
        """
        # initalize manifest generation class
        mg = ManifestGeneration(schema_url=schema_url, access_token=access_token, output_format=output_format, title=title, use_annotations=use_annotations, dataset_id=dataset_id, data_type=data_type, asset_view=asset_view)

        # load configuration file
        mg._load_config_()

        # get path to temporary JSON-LD file
        jsonld = get_temp_jsonld(schema_url)

        if dataset_id: 
            mg._check_dataset_match_datatype_(dataset_id, data_type)
            
            # Raise an error if used in conjunction with datatype = 'all_manifests'
            try:
                data_type[0] != 'all manifests'
            except:
                raise ValueError(
                        f"When submitting 'all manifests' as the data_type cannot also submit dataset_id. "
                        f"Please check your submission and try again."
                    )

        # Gather all returned result urls
        if data_type[0] == 'all manifests':
            sg = SchemaGenerator(path_to_json_ld=jsonld)
            component_digraph = sg.se.get_digraph_by_edge_type('requiresComponent')
            components = list(component_digraph.nodes())
        else:
            components = data_type

        # when output_format = "google_sheet" or output_format = "data_frame", a list of outputs get returned if needed
        all_outputs = mg.generate_manifest_and_collect_outputs(data_type_lst=components, dataset_id=dataset_id)

        if all_outputs:
            return all_outputs
            
        # all_results = []
        # components = component_digraph.nodes()
        # for component in components:
        #     t = mg._get_manifest_title(single_data_type=component)
        #     if output_format != "excel":
        #         output = mg.create_single_manifest(single_data_type=component, title=t)
        #     else:
        #         all_results.append(output)
            # else: 
            #     app.logger.error('Currently we do not support returning multiple files as Excel format at once. Please choose a different output format. ')
        #else:
            # for i, dt in enumerate(data_type):
            #     t = mg._get_manifest_title(single_data_type=dt)
            #     if dataset_id:
            #         # get existing manifest
            #         output = mg.create_single_manifest(single_data_type=dt, single_dataset_id=dataset_id[i], title=t)
            #     else:
            #         output = mg.create_single_manifest(single_data_type=dt, title=t)

                # # if output is pandas dataframe or google sheet url
                # if isinstance(result, str) or isinstance(result, pd.DataFrame):
                #     all_results.append(result)
                # else: 
                #     if len(data_type) > 1:
                #         app.logger.warning(f'Currently we do not support returning multiple files as Excel format at once. Only {t} would get returned. ')
                #     return result

        #return all_outputs

#####profile validate manifest route function 
#@profile(sort_by='cumulative', strip_dirs=True)
def validate_manifest_route(schema_url, data_type, restrict_rules=None, json_str=None):
    # if restrict rules is set to None, default it to False
    if not restrict_rules:
        restrict_rules=False
        
    # call config_handler()
    config_handler()

    #If restrict_rules parameter is set to None, then default it to False 
    if not restrict_rules:
        restrict_rules = False

    #Get path to temp file where manifest file contents will be saved
    jsc = JsonConverter()

    if json_str:
        temp_path = jsc.convert_json_str_to_csv(json_str = json_str, file_name = "example_json")
    else: 
        temp_path = jsc.convert_json_file_to_csv("file_name")

    # get path to temporary JSON-LD file
    jsonld = get_temp_jsonld(schema_url)

    metadata_model = MetadataModel(
        inputMModelLocation=jsonld, inputMModelLocationType="local"
    )

    errors, warnings = metadata_model.validateModelManifest(
        manifestPath=temp_path, rootNode=data_type, restrict_rules=restrict_rules
    )
    
    res_dict = {"errors": errors, "warnings": warnings}

    return res_dict

#####profile validate manifest route function 
#@profile(sort_by='cumulative', strip_dirs=True)
def submit_manifest_route(schema_url, asset_view=None, manifest_record_type=None, json_str=None, table_manipulation=None, data_type=None):
    # call config_handler()
    config_handler(asset_view = asset_view)

    # convert Json file to CSV if applicable
    jsc = JsonConverter()
    if json_str:
        temp_path = jsc.convert_json_str_to_csv(json_str = json_str, file_name = "example_json.csv")
    else: 
        temp_path = jsc.convert_json_file_to_csv("file_name")

    dataset_id = connexion.request.args["dataset_id"]

    restrict_rules = parse_bool(connexion.request.args["restrict_rules"])

    metadata_model = initalize_metadata_model(schema_url)

    access_token = connexion.request.args["access_token"]


    use_schema_label = connexion.request.args["use_schema_label"]
    if use_schema_label == 'None':
        use_schema_label = True
    else:
        use_schema_label = parse_bool(use_schema_label)

    if not table_manipulation: 
        table_manipulation = "replace"

    if not manifest_record_type:
        manifest_record_type = "table_file_and_entities"

    if data_type == 'None':
        validate_component = None
    else:
        validate_component = data_type

    manifest_id = metadata_model.submit_metadata_manifest(
        path_to_json_ld = schema_url, 
        manifest_path=temp_path, 
        dataset_id=dataset_id, 
        validate_component=validate_component, 
        access_token=access_token, 
        manifest_record_type = manifest_record_type, 
        restrict_rules = restrict_rules, 
        table_manipulation = table_manipulation, 
        use_schema_label=use_schema_label)

    return manifest_id

def populate_manifest_route(schema_url, title=None, data_type=None, return_excel=None):
    # call config_handler()
    config_handler()

    # get path to temporary JSON-LD file
    jsonld = get_temp_jsonld(schema_url)

    # Get path to temp file where manifest file contents will be saved
    temp_path = save_file()
   
    #Initalize MetadataModel
    metadata_model = MetadataModel(inputMModelLocation=jsonld, inputMModelLocationType='local')

    #Call populateModelManifest class
    populated_manifest_link = metadata_model.populateModelManifest(title=title, manifestPath=temp_path, rootNode=data_type, return_excel=return_excel)

    return populated_manifest_link

def get_storage_projects(access_token, asset_view):
    # call config handler 
    config_handler(asset_view=asset_view)

    # use Synapse storage 
    store = SynapseStorage(access_token=access_token)

    # call getStorageProjects function
    lst_storage_projects = store.getStorageProjects()
    
    return lst_storage_projects

def get_storage_projects_datasets(access_token, asset_view, project_id):
    # call config handler
    config_handler(asset_view=asset_view)

    # use Synapse Storage
    store = SynapseStorage(access_token=access_token)

    # call getStorageDatasetsInProject function
    sorted_dataset_lst = store.getStorageDatasetsInProject(projectId = project_id)
    
    return sorted_dataset_lst

def get_files_storage_dataset(access_token, asset_view, dataset_id, full_path, file_names=None):
    # call config handler
    config_handler(asset_view=asset_view)

    # use Synapse Storage
    store = SynapseStorage(access_token=access_token)

    # no file names were specified (file_names = [''])
    if file_names and not all(file_names): 
        file_names=None
    
    # call getFilesInStorageDataset function
    file_lst = store.getFilesInStorageDataset(datasetId=dataset_id, fileNames=file_names, fullpath=full_path)
    return file_lst

def check_if_files_in_assetview(access_token, asset_view, entity_id):
    # call config handler 
    config_handler(asset_view=asset_view)

    # use Synapse Storage
    store = SynapseStorage(access_token=access_token)

    # call function and check if a file or a folder is in asset view
    if_exists = store.checkIfinAssetView(entity_id)

    return if_exists

def check_entity_type(access_token, entity_id):
    # call config handler 
    config_handler()

    syn = SynapseStorage.login(access_token = access_token)
    entity_type = entity_type_mapping(syn, entity_id)

    return entity_type 

def get_component_requirements(schema_url, source_component, as_graph):
    metadata_model = initalize_metadata_model(schema_url)

    req_components = metadata_model.get_component_requirements(source_component=source_component, as_graph = as_graph)

    return req_components

@cross_origin(["http://localhost", "https://sage-bionetworks.github.io"])
def get_viz_attributes_explorer(schema_url):
    # call config_handler()
    config_handler()

    temp_path_to_jsonld = get_temp_jsonld(schema_url)

    attributes_csv = AttributesExplorer(temp_path_to_jsonld).parse_attributes(save_file=False)

    return attributes_csv

def get_viz_component_attributes_explorer(schema_url, component, include_index):
    # call config_handler()
    config_handler()

    temp_path_to_jsonld = get_temp_jsonld(schema_url)

    attributes_csv = AttributesExplorer(temp_path_to_jsonld).parse_component_attributes(component, save_file=False, include_index=include_index)

    return attributes_csv

@cross_origin(["http://localhost", "https://sage-bionetworks.github.io"])
def get_viz_tangled_tree_text(schema_url, figure_type, text_format):
   
    temp_path_to_jsonld = get_temp_jsonld(schema_url)

    # Initialize TangledTree
    tangled_tree = TangledTree(temp_path_to_jsonld, figure_type)

    # Get text for tangled tree.
    text_df = tangled_tree.get_text_for_tangled_tree(text_format, save_file=False)
    
    return text_df

@cross_origin(["http://localhost", "https://sage-bionetworks.github.io"])
def get_viz_tangled_tree_layers(schema_url, figure_type):

    # call config_handler()
    config_handler()

    temp_path_to_jsonld = get_temp_jsonld(schema_url)

    # Initialize Tangled Tree
    tangled_tree = TangledTree(temp_path_to_jsonld, figure_type)
    
    # Get tangled trees layers JSON.
    layers = tangled_tree.get_tangled_tree_layers(save_file=False)

    return layers[0]

def download_manifest(access_token, manifest_id, new_manifest_name='', as_json=True):
    """
    Download a manifest based on a given manifest id. 
    Args:
        access_token: token of asset store
        manifest_syn_id: syn id of a manifest
        newManifestName: new name of a manifest that gets downloaded.
        as_json: boolean; If true, return a manifest as a json. Default to True
    Return: 
        file path of the downloaded manifest
    """
    # call config_handler()
    config_handler()

    # use Synapse Storage
    store = SynapseStorage(access_token=access_token)
    # try logging in to asset store
    syn = store.login(access_token=access_token)
    try: 
        md = ManifestDownload(syn, manifest_id)
        manifest_data = ManifestDownload.download_manifest(md, new_manifest_name)
        #return local file path
        manifest_local_file_path = manifest_data['path']
    except TypeError as e:
        raise TypeError(f'Failed to download manifest {manifest_id}.')
    if as_json:
        manifest_json = return_as_json(manifest_local_file_path)
        return manifest_json
    else:
        return manifest_local_file_path

#@profile(sort_by='cumulative', strip_dirs=True)  
def download_dataset_manifest(access_token, dataset_id, asset_view, as_json, new_manifest_name=''):
    # call config handler
    config_handler(asset_view=asset_view)

    # use Synapse Storage
    store = SynapseStorage(access_token=access_token)

    # download existing file
    manifest_data = store.getDatasetManifest(datasetId=dataset_id, downloadFile=True, newManifestName=new_manifest_name)

    #return local file path
    try:
        manifest_local_file_path = manifest_data['path']

    except KeyError as e:
        raise KeyError(f'Failed to download manifest from dataset: {dataset_id}') from e

    #return a json (if as_json = True)
    if as_json:
        manifest_json = return_as_json(manifest_local_file_path)
        return manifest_json

    return manifest_local_file_path

def get_asset_view_table(access_token, asset_view, return_type):
    # call config handler
    config_handler(asset_view=asset_view)

    # use Synapse Storage
    store = SynapseStorage(access_token=access_token)

    # get file view table
    file_view_table_df = store.getStorageFileviewTable()

    # return different results based on parameter
    if return_type == "json":
        json_res = file_view_table_df.to_json()
        return json_res
    else:
        path = os.getcwd()
        export_path = os.path.join(path, 'tests/data/file_view_table.csv')
        file_view_table_df.to_csv(export_path, index=False)
        return export_path


def get_project_manifests(access_token, project_id, asset_view):
    # use the default asset view from config
    config_handler(asset_view=asset_view)

    # use Synapse Storage
    store = SynapseStorage(access_token=access_token)

    # call getprojectManifest function
    lst_manifest = store.getProjectManifests(projectId=project_id)

    return lst_manifest

def get_manifest_datatype(access_token, manifest_id, asset_view):
    # use the default asset view from config
    config_handler(asset_view=asset_view)

    # use Synapse Storage
    store = SynapseStorage(access_token=access_token)

    # get data types of an existing manifest
    manifest_dtypes_dict= store.getDataTypeFromManifest(manifest_id)


    return manifest_dtypes_dict

def get_schema_pickle(schema_url):
    # load schema
    se = SchemaExplorer()

    se.load_schema(schema_url)

    # get schema
    schema_graph = se.get_nx_schema()

    # write to local pickle file
    path = os.getcwd()
    export_path = os.path.join(path, 'tests/data/schema.gpickle')

    with open(export_path, 'wb') as file:
        pickle.dump(schema_graph, file)
    return export_path


def get_subgraph_by_edge_type(schema_url, relationship):
    # use schema generator and schema explorer
    sg = SchemaGenerator(path_to_json_ld=schema_url)
    se = SchemaExplorer()
    se.load_schema(schema_url)

    # get the schema graph 
    schema_graph = se.get_nx_schema()

    # relationship subgraph
    relationship_subgraph = sg.get_subgraph_by_edge_type(schema_graph, relationship)

    # return relationship 
    Arr = []
    for t in relationship_subgraph.edges:
        lst = list(t)
        Arr.append(lst)

    return Arr


def find_class_specific_properties(schema_url, schema_class):
    # use schema explorer
    se = SchemaExplorer()

    # load schema
    se.load_schema(schema_url)

    # return properties
    properties = se.find_class_specific_properties(schema_class)

    return properties


def get_node_dependencies(
    schema_url: str,
    source_node: str,
    return_display_names: bool = True,
    return_schema_ordered: bool = True
) -> list[str]:
    """Get the immediate dependencies that are related to a given source node.

    Args:
        schema_url (str): Data Model URL
        source_node (str): The node whose dependencies are needed.
        return_display_names (bool, optional):
            If True, return list of display names of each of the dependencies.
            If False, return list of node labels of each of the dependencies.
            Defaults to True.
        return_schema_ordered (bool, optional):
            If True, return the dependencies of the node following the order of the schema (slower).
            If False, return dependencies from graph without guaranteeing schema order (faster).
            Defaults to True.

    Returns:
        list[str]: List of nodes that are dependent on the source node.
    """
    gen = SchemaGenerator(path_to_json_ld=schema_url)
    dependencies = gen.get_node_dependencies(
        source_node, return_display_names, return_schema_ordered
    )
    return dependencies


def get_property_label_from_display_name(
    schema_url: str,
    display_name: str,
    strict_camel_case: bool = False
) -> str:
    """Converts a given display name string into a proper property label string

    Args:
        schema_url (str): Data Model URL
        display_name (str): The display name to be converted
        strict_camel_case (bool, optional): If true the more strict way of
            converting to camel case is used.

    Returns:
        str: The property label of the display name
    """
    explorer = SchemaExplorer()
    explorer.load_schema(schema_url)
    label = explorer.get_property_label_from_display_name(display_name, strict_camel_case)
    return label


def get_node_range(
    schema_url: str,
    node_label: str,
    return_display_names: bool = True
) -> list[str]:
    """Get the range, i.e., all the valid values that are associated with a node label.

    Args:
        schema_url (str): Data Model URL
        node_label (str): Node / term for which you need to retrieve the range.
        return_display_names (bool, optional): If true returns the display names of the nodes.
            Defaults to True.

    Returns:
        list[str]: A list of nodes
    """
    gen = SchemaGenerator(path_to_json_ld=schema_url)
    node_range = gen.get_node_range(node_label, return_display_names)
    return node_range

def get_if_node_required(schema_url: str, node_display_name: str) -> bool:
    """Check if the node is required

    Args:
        schema_url (str): Data Model URL
        node_display_name (str): display name

    Returns:
        True: If the given node is a "required" node.
        False: If the given node is not a "required" (i.e., an "optional") node.
    """
    gen = SchemaGenerator(path_to_json_ld=schema_url)
    is_required = gen.is_node_required(node_display_name)

    return is_required

def get_node_validation_rules(schema_url: str, node_display_name: str) -> list:
    """
    Args:
        schema_url (str): Data Model URL
        node_display_name (str): node display name
    Returns:
        List of valiation rules for a given node.
    """
    gen = SchemaGenerator(path_to_json_ld=schema_url)
    node_validation_rules = gen.get_node_validation_rules(node_display_name)

    return node_validation_rules

def get_nodes_display_names(schema_url: str, node_list: list[str]) -> list:
    """From a list of node labels retrieve their display names, return as list.
    
    Args:
        schema_url (str): Data Model URL
        node_list (List[str]): List of node labels.
        
    Returns:
        node_display_names (List[str]): List of node display names.

    """
    gen = SchemaGenerator(path_to_json_ld=schema_url)
    mm_graph = gen.se.get_nx_schema()
    node_display_names = gen.get_nodes_display_names(node_list, mm_graph)
    return node_display_names

