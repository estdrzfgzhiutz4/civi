import logging
import time
import urllib.parse

from common.tools import Tools
from models.model import Model

class MetadataExtractor:
    '''
    Class to process the model data and download files from CivitAI.
    '''
    def __init__(self, token:str='', max_tries:int=5, retry_delay:int=20):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)

        self.base_url = "https://civitai.com/api/v1/models"
        self.token = token
        self.max_tries = max_tries
        self.retry_delay = retry_delay


    def extract(self, usernames:list=None, model_ids:list=None, max_gallery_images_per_model:int=20) -> dict[str, Model]:
        '''
        Extract all models for a given user or model ID.
        '''
        result = {}

        if usernames is not None:
            for u in usernames:
                for m in self.__extract_user(u):
                    if m.id not in result:
                            result[m.id] = m
        
        if model_ids is not None:
            for m in model_ids:
                if m not in result:
                    model_data = self.__extract_model(m)
                    if model_data is not None:
                        result[m] = model_data

        # Enrich each resolved model with up to N gallery images that include per-image metadata.
        if max_gallery_images_per_model is not None and max_gallery_images_per_model > 0:
            for model in result.values():
                self.__attach_gallery_images(model, max_gallery_images_per_model)

        return result


    def __extract_user(self, username:str):
        '''
        Extract all models for a given user.
        '''
        models = []

        query_string = urllib.parse.urlencode({ "username": username, "nsfw": "true" }) 
        page = f"{self.base_url}?{query_string}"

        while True:
            if page is None:
                self.logger.info("End of pagination reached: 'next_page' is None.")
                break

            data = Tools.get_json_with_retry(self.logger, page, self.token, self.retry_delay, self.max_tries)

            if not data:
                self.logger.warning(f"Fetching %s or %s return invalid result, skipped.", page, username)
                return None
            else:
                for model in data['items']:
                    models.append(Model(model))

                metadata = data.get('metadata', {})
                page = metadata.get('nextPage')

                if not metadata and not data['items']:
                    self.logger.warning("Termination condition met: 'metadata' is empty.")
                    break

                time.sleep(2)  # Respect API rate limits

        return models


    def __extract_model(self, model_id:str):
        '''
        Exctract all models for a model.
        '''
        query_string = urllib.parse.urlencode({ "nsfw": "true" })
        data = Tools.get_json_with_retry(self.logger, f"{self.base_url}/{model_id}?{query_string}", self.token, self.retry_delay)

        time.sleep(2)  # Respect API rate limits

        if not data:
            self.logger.warning(f"Fetching model with id: %s returned invalid result, skipped.", model_id)
            return None   
        else:
            return Model(data)

    def __extract_gallery_images(self, model_id:str, max_images:int) -> list[dict]:
        '''
        Extract up to max_images gallery images for a model.
        '''
        images = []
        query_string = urllib.parse.urlencode(
            {"modelId": model_id, "limit": min(max_images, 200), "nsfw": "true"}
        )
        page = f"https://civitai.com/api/v1/images?{query_string}"

        while page and len(images) < max_images:
            data = Tools.get_json_with_retry(
                self.logger, page, self.token, self.retry_delay, self.max_tries
            )
            if not data:
                break

            for image in data.get("items", []):
                images.append(image)
                if len(images) >= max_images:
                    break

            page = data.get("metadata", {}).get("nextPage")
            if page and len(images) < max_images:
                time.sleep(1)

        return images

    def __attach_gallery_images(self, model:Model, max_images:int) -> None:
        '''
        Attach gallery images to each version for a model.
        '''
        images = self.__extract_gallery_images(str(model.id), max_images)
        if not images:
            return

        version_lookup = {str(version.id): version for version in model.versions}
        counts: dict[str, int] = {}

        for image in images:
            version_id = str(image.get("modelVersionId", ""))
            if version_id == "" or version_id not in version_lookup:
                continue

            current_count = counts.get(version_id, 0)
            if current_count >= max_images:
                continue

            version_lookup[version_id].add_asset(image)
            counts[version_id] = current_count + 1
