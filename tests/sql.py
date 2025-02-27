import os
import tempfile

import pytest
from sqlalchemy import create_engine


@pytest.fixture(scope='class')
def engine():
    # Tensorflow's `from_generator` tends to cause issues with in-memory sqlite databases due to threading,
    # so here (and likely in other small codes) we'll want to make sure we at least write to a local file.

    file = os.path.join(tempfile.gettempdir(), os.urandom(24).hex())
    engine = create_engine(
        f'sqlite:///{file}',
        connect_args={'check_same_thread': False},
    )
    yield engine
    engine.dispose()
    try:
        os.remove(file)
    except PermissionError:
        # todo, figure out why files not deleted on windows
        pass
