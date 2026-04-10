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

            data = Tools.get_json_with_retry(
                self.logger,
                page,
                self.token,
                self.retry_delay,
                max_retries=self.max_tries,
            )

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
        data = Tools.get_json_with_retry(
            self.logger,
            f"{self.base_url}/{model_id}?{query_string}",
            self.token,
            self.retry_delay,
            max_retries=self.max_tries,
        )

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
                self.logger,
                page,
                self.token,
                self.retry_delay,
                max_retries=self.max_tries,
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
        if not version_lookup:
            return

        counts: dict[str, int] = {}
        attached_image_ids: set[str] = set()

        for image in images:
            image_id = str(image.get("id", ""))
            if image_id != "" and image_id in attached_image_ids:
                continue

            candidate_version_ids: list[str] = []

            # Most common payload shape.
            if image.get("modelVersionId") is not None:
                candidate_version_ids.append(str(image.get("modelVersionId")))

            # Alternative payload shape used by some image endpoints.
            if isinstance(image.get("modelVersionIds"), list):
                candidate_version_ids.extend(str(v) for v in image.get("modelVersionIds"))

            # Fallback shape where model versions are nested objects.
            if isinstance(image.get("modelVersions"), list):
                for item in image.get("modelVersions"):
                    if isinstance(item, dict) and item.get("id") is not None:
                        candidate_version_ids.append(str(item.get("id")))

            # Keep order, remove empties/duplicates.
            candidate_version_ids = [v for i, v in enumerate(candidate_version_ids) if v and v not in candidate_version_ids[:i]]

            selected_version_id = None
            for version_id in candidate_version_ids:
                if version_id in version_lookup:
                    selected_version_id = version_id
                    break

            # If no version mapping was supplied by API, attach to the first version so the image is still archived.
            if selected_version_id is None:
                selected_version_id = str(model.versions[0].id)

            current_count = counts.get(selected_version_id, 0)
            if current_count >= max_images:
                continue

            image_with_source = dict(image)
            image_with_source["_source"] = "gallery"
            version_lookup[selected_version_id].add_asset(image_with_source)
            counts[selected_version_id] = current_count + 1
            if image_id != "":
                attached_image_ids.add(image_id)
