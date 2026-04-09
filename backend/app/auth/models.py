from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    token_type: str
    role: str


class TokenData(BaseModel):
    username: str
    role: str


class UserInDB(BaseModel):
    username: str
    hashed_password: str
    role: str
