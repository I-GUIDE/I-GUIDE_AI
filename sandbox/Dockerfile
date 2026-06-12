# Pre-baked sandbox image for agent code execution (AGENT_CODE_EXEC_IMAGE).
#
#   docker build -t iguide-codeexec sandbox/
#   export AGENT_CODE_EXEC_IMAGE=iguide-codeexec
#
# With the common scientific stack already installed, queries like
# "plot y=x^2 and save plot.png" run WITHOUT a per-run `pip install`, which avoids
# the install-phase disk/time cost entirely. The agent only needs `dependencies`
# for packages not baked here.
FROM python:3.11-slim

ENV MPLBACKEND=Agg \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir \
    numpy \
    pandas \
    matplotlib \
    seaborn \
    scipy \
    shapely

# Pre-build the matplotlib font cache so first-run plotting is fast and needs no
# writable HOME beyond /tmp.
RUN python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; plt.plot([0,1]); plt.savefig('/tmp/_warm.png')" && rm -f /tmp/_warm.png

# For geospatial code (geopandas/fiona/rasterio) uncomment:
# RUN apt-get update && apt-get install -y gdal-bin libgdal-dev \
#     && rm -rf /var/lib/apt/lists/* \
#     && CPLUS_INCLUDE_PATH=/usr/include/gdal C_INCLUDE_PATH=/usr/include/gdal \
#        pip install --no-cache-dir geopandas fiona rasterio
