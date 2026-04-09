from pathlib import Path
import urllib.parse

class Asset:
    '''
    Class representing a machine learning model.
    Each model can have multiple versions and tasks associated with it.
    '''
    def __init__(self, version, asset:dict):
        '''
        Initialize the model with url.
        '''
        self.version = version

        self.url = asset.get('url', '')
        self.type = asset.get('type', 'Unknown')
        self.metadata = asset.get('meta', {})
        self.id = str(asset.get('id', ''))
        self.model_version_id = str(asset.get('modelVersionId', ''))

        if self.url != '':
            parsed = urllib.parse.urlparse(self.url)
            path = Path(parsed.path)
            if self.id == '':
                self.id = path.stem
            self.extension = path.suffix
            self.name = path.name
        else:
            self.extension = ''
            self.name = ''

        self.output_path = self.version.output_path
