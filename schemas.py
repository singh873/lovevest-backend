from pydantic import BaseModel, EmailStr, Field, field_validator
import re
from typing import Optional


class UserRegister(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    age: int = Field(ge=18, le=100)
    gender: str = Field(min_length=2, max_length=20)
    state: str = Field(min_length=2, max_length=50)
    user_id: str = Field(min_length=4, max_length=30)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    @field_validator("name", "gender", "state")
    @classmethod
    def validate_text(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str):
        if not re.fullmatch(r"[A-Za-z ]+", value):
            raise ValueError("Name can contain only letters and spaces")
        return value

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str):
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9_]+", value):
            raise ValueError("User ID can contain only letters, numbers and underscore")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str):
        if value != value.strip():
            raise ValueError("Password cannot start or end with spaces")
        if not re.search(r"[A-Z]", value):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", value):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", value):
            raise ValueError("Password must contain at least one number")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>_\-]", value):
            raise ValueError("Password must contain at least one special character")
        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: str

class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=50)
    age: Optional[int] = Field(default=None, ge=18, le=100)
    gender: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None, min_length=2, max_length=50)
    email: Optional[EmailStr] = None

class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=100)

class PartnerInvite(BaseModel):
    user_id: str
class RelationshipRequestAction(BaseModel):
    request_id: int