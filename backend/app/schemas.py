"""Request and response schemas."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    role: str
    preferences: dict


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class PreferencesPatch(BaseModel):
    preferences: dict


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    status: str
    progress: int
    stage: str
    error: str | None
    stats: dict
    created_at: datetime
    updated_at: datetime
    analyzed_at: datetime | None


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    conversation_id: int | None = Field(None, description="Continue this conversation; omitted = the latest one")
    new_conversation: bool = Field(False, description="Start a new conversation with this question")


# Document Q&A: the four answer perspectives. These style the answer; they are unrelated to User.role.
DocRole = Literal["end_user", "tester", "developer", "business"]


class DocAskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    role: DocRole = "end_user"
    conversation_id: int | None = Field(None, description="Continue this conversation; omitted = the latest one")
    new_conversation: bool = Field(False, description="Start a new conversation with this question")


class DocCompareRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    roles: list[DocRole] | None = Field(default=None, description="Defaults to all four roles when omitted")


class ImpactRequest(BaseModel):
    target: str = Field(min_length=1, max_length=512, description="A node id or a name such as CUSTOMER_ADDRESS")


class FileImpactRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024, description="A source file path as listed by GET /files")


class ImpactExplainRequest(BaseModel):
    """Exactly one of target (a node id or name) or path (a source file)."""
    target: str | None = Field(None, max_length=512)
    path: str | None = Field(None, max_length=1024)


class ModernizationRequest(BaseModel):
    language: Literal["java", "dotnet", "python"] = "java"
    database: Literal["postgresql", "azure_sql", "oracle", "mysql"] = "postgresql"
    platform: Literal["azure", "aws", "gcp", "on_prem"] = "azure"


class AISettingsUpdate(BaseModel):
    provider: str | None = None
    model: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=1024)
    api_version: str | None = Field(default=None, max_length=64)
    deployment: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    temperature: float | None = Field(default=None, ge=0, le=1)
    max_output_tokens: int | None = Field(default=None, ge=128, le=8192)
    top_k: int | None = Field(default=None, ge=1, le=20)
    context_char_budget: int | None = Field(default=None, ge=2000, le=60000)
    min_retrieval_score: float | None = Field(default=None, ge=0, le=20)
    monthly_token_budget: int | None = Field(default=None, ge=0, description="0 removes the cap")
    project_token_budget: int | None = Field(default=None, ge=0, description="0 removes the cap")



class AIModelIn(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    provider: str | None = None
    model: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=1024)
    api_version: str | None = Field(default=None, max_length=64)
    deployment: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    is_small: bool | None = None
    is_fallback: bool | None = None


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=8, max_length=255)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=8, max_length=255)


class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)


class UserAdminUpdate(BaseModel):
    role: Literal["admin", "analyst", "viewer"] | None = None
    is_active: bool | None = None


class RuleReview(BaseModel):
    status: Literal["candidate", "accepted", "rejected"]
    note: str = Field(default="", max_length=2000)
    reason: Literal["", "technical", "obsolete", "duplicate", "other"] = ""


class EmbeddingSettingsUpdate(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    model: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=1024)
    api_version: str | None = Field(default=None, max_length=64)
    deployment: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    reuse_llm_key: bool | None = None
    batch_size: int | None = Field(default=None, ge=1, le=256)
    max_chars: int | None = Field(default=None, ge=500, le=30000)
    semantic_weight: float | None = Field(default=None, ge=0, le=5)
    min_semantic_score: float | None = Field(default=None, ge=0, le=1)
    auto_index: bool | None = None
