from googleapiclient.discovery import build


def build_sheets_service(creds_dict: dict):
    from app.auth.oauth import credentials_from_session

    creds = credentials_from_session(creds_dict)
    return build("sheets", "v4", credentials=creds)


def build_drive_service(creds_dict: dict):
    """Used only to delete a half-built spreadsheet when a dashboard build
    fails partway (drive.file scope: the app can only touch files it created)."""
    from app.auth.oauth import credentials_from_session

    creds = credentials_from_session(creds_dict)
    return build("drive", "v3", credentials=creds)
