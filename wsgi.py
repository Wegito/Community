from dotenv import load_dotenv

load_dotenv()

from community import create_app  # noqa: E402  (.env must be loaded before the config is read)

app = create_app()
