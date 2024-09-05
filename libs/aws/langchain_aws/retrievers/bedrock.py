from typing import Any, Dict, List, Literal, Optional, Union

import boto3
from botocore.client import Config
from botocore.exceptions import UnknownServiceError
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from pydantic import BaseModel, Field, root_validator
from langchain_core.retrievers import BaseRetriever
from typing_extensions import Annotated
from pydantic import ConfigDict


FilterValue = Union[Dict[str, Any], List[Any], int, float, str, bool, None]
Filter = Dict[str, FilterValue]


class SearchFilter(BaseModel):
    """Filter configuration for retrieval."""

    andAll: Optional[List["SearchFilter"]] = None
    orAll: Optional[List["SearchFilter"]] = None
    equals: Optional[Filter] = None
    greaterThan: Optional[Filter] = None
    greaterThanOrEquals: Optional[Filter] = None
    in_: Optional[Filter] = Field(None, alias="in")
    lessThan: Optional[Filter] = None
    lessThanOrEquals: Optional[Filter] = None
    listContains: Optional[Filter] = None
    notEquals: Optional[Filter] = None
    notIn: Optional[Filter] = Field(None, alias="notIn")
    startsWith: Optional[Filter] = None
    stringContains: Optional[Filter] = None

    model_config = ConfigDict(populate_by_name=True,)


class VectorSearchConfig(BaseModel, extra="allow"):  # type: ignore[call-arg]
    """Configuration for vector search."""

    numberOfResults: int = 4
    filter: Optional[SearchFilter] = None
    overrideSearchType: Optional[Literal["HYBRID", "SEMANTIC"]] = None


class RetrievalConfig(BaseModel, extra="allow"):  # type: ignore[call-arg]
    """Configuration for retrieval."""

    vectorSearchConfiguration: VectorSearchConfig
    nextToken: Optional[str] = None


class AmazonKnowledgeBasesRetriever(BaseRetriever):
    """`Amazon Bedrock Knowledge Bases` retrieval.

        See https://aws.amazon.com/bedrock/knowledge-bases for more info.

        Args:
            knowledge_base_id: Knowledge Base ID.
            region_name: The aws region e.g., `us-west-2`.
                Fallback to AWS_DEFAULT_REGION env variable or region specified in
                ~/.aws/config.
            credentials_profile_name: The name of the profile in the ~/.aws/credentials
                or ~/.aws/config files, which has either access keys or role information
                specified. If not specified, the default credential profile or, if on an
                EC2 instance, credentials from IMDS will be used.
            client: boto3 client for bedrock agent runtime.
            retrieval_config: Configuration for retrieval.

        Example:
            .. code-block:: python

                from langchain_community.retrievers import AmazonKnowledgeBasesRetriever

    retriever = AmazonKnowledgeBasesRetriever(
        knowledge_base_id="<knowledge-base-id>",
        retrieval_config={
            "vectorSearchConfiguration": {
                "numberOfResults": 4
            }
        },
    )
    """

    knowledge_base_id: str
    region_name: Optional[str] = None
    credentials_profile_name: Optional[str] = None
    endpoint_url: Optional[str] = None
    client: Any
    retrieval_config: RetrievalConfig
    min_score_confidence: Annotated[Optional[float], Field(ge=0.0, le=1.0)]

    @root_validator(pre=True)
    def create_client(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if values.get("client") is not None:
            return values

        try:
            if values.get("credentials_profile_name"):
                session = boto3.Session(profile_name=values["credentials_profile_name"])
            else:
                # use default credentials
                session = boto3.Session()

            client_params = {
                "config": Config(
                    connect_timeout=120, read_timeout=120, retries={"max_attempts": 0}
                )
            }
            if values.get("region_name"):
                client_params["region_name"] = values["region_name"]

            if values.get("endpoint_url"):
                client_params["endpoint_url"] = values["endpoint_url"]

            values["client"] = session.client("bedrock-agent-runtime", **client_params)

            return values
        except ImportError:
            raise ModuleNotFoundError(
                "Could not import boto3 python package. "
                "Please install it with `pip install boto3`."
            )
        except UnknownServiceError as e:
            raise ModuleNotFoundError(
                "Ensure that you have installed the latest boto3 package "
                "that contains the API for `bedrock-runtime-agent`."
            ) from e
        except Exception as e:
            raise ValueError(
                "Could not load credentials to authenticate with AWS client. "
                "Please check that credentials in the specified "
                "profile name are valid."
            ) from e

    def _filter_by_score_confidence(self, docs: List[Document]) -> List[Document]:
        """
        Filter out the records that have a score confidence
        less than the required threshold.
        """
        if not self.min_score_confidence:
            return docs
        filtered_docs = [
            item
            for item in docs
            if (
                item.metadata.get("score") is not None
                and item.metadata.get("score", 0.0) >= self.min_score_confidence
            )
        ]
        return filtered_docs

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> List[Document]:
        response = self.client.retrieve(
            retrievalQuery={"text": query.strip()},
            knowledgeBaseId=self.knowledge_base_id,
            retrievalConfiguration=self.retrieval_config.dict(exclude_none=True),
        )
        results = response["retrievalResults"]
        documents = []
        for result in results:
            content = result["content"]["text"]
            result.pop("content")
            if "score" not in result:
                result["score"] = 0
            if "metadata" in result:
                result["source_metadata"] = result.pop("metadata")
            documents.append(
                Document(
                    page_content=content,
                    metadata=result,
                )
            )

        return self._filter_by_score_confidence(docs=documents)
