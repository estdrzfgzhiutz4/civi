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

        # Attach up to N community gallery images from the images feed.
        if max_gallery_images_per_model is not None and max_gallery_images_per_model > 0:
            for model in result.values():
                self.__attach_gallery_images_from_feed(model, max_gallery_images_per_model)

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

    def __fetch_gallery_feed(self, model_version_id:str, page_size:int=100, max_pages:int=5) -> list[dict]:
        '''
        Fetch image feed entries for a specific model version.
        '''
        images = []
        query_string = urllib.parse.urlencode(
            {
                "modelVersionId": model_version_id,
                "limit": min(page_size, 200),
                "sort": "Newest",
                "period": "AllTime",
                "nsfw": "true",
            }
        )
        page = f"https://civitai.com/api/v1/images?{query_string}"
        page_count = 0

        while page and page_count < max_pages:
            data = Tools.get_json_with_retry(
                self.logger,
                page,
                self.token,
                self.retry_delay,
                max_retries=self.max_tries,
            )
            if not data:
                break

            items = data.get("items", [])
            if not items:
                break

            images.extend(items)
            page_count += 1
            page = data.get("metadata", {}).get("nextPage")
            if page and page_count < max_pages:
                time.sleep(1)

        return images

    def __attach_gallery_images_from_feed(self, model:Model, max_images:int) -> None:
        '''
        Attach gallery images from /images feed while filtering strictly to this model/version.
        '''
        version_lookup = {str(v.id): v for v in model.versions}
        attached = 0
        seen: set[str] = set()

        for version in model.versions:
            if attached >= max_images:
                break

            version_id = str(version.id)
            images = self.__fetch_gallery_feed(version_id)

            for image in images:
                image_id = str(image.get("id", ""))
                if image_id and image_id in seen:
                    continue

                # Strictly keep only images that mention this version in canonical fields.
                candidate_version_ids = set()
                if image.get("modelVersionId") is not None:
                    candidate_version_ids.add(str(image.get("modelVersionId")))
                if isinstance(image.get("modelVersionIds"), list):
                    candidate_version_ids.update(str(v) for v in image.get("modelVersionIds"))
                meta = image.get("meta") or {}
                resources = meta.get("resources") or meta.get("civitaiResources") or []
                if isinstance(resources, list):
                    for resource in resources:
                        if isinstance(resource, dict) and resource.get("modelVersionId") is not None:
                            candidate_version_ids.add(str(resource.get("modelVersionId")))

                if version_id not in candidate_version_ids:
                    continue

                image_with_source = dict(image)
                image_with_source["_source"] = "gallery"
                version_lookup[version_id].add_asset(image_with_source)
                attached += 1

                if image_id:
                    seen.add(image_id)
                if attached >= max_images:
                    break

        self.logger.info(
            "Attached %d gallery image(s) from feed for model %s (%s).",
            attached,
            model.id,
            model.name,
        )
