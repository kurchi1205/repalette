# Installation
```
conda install -n repalette python pytorch torchvision scikit-learn scikit-image requests beautifulsoup4 jupyterlab ipywidgets opencv pandas tqdm -c conda-forge -c pytorch

# Avoid "JavaScript heap out of memory" errors during extension installation
# (OS X/Linux)
export NODE_OPTIONS=--max-old-space-size=4096
# (Windows)
set NODE_OPTIONS=--max-old-space-size=4096
# Jupyter widgets extension
jupyter labextension install @jupyter-widgets/jupyterlab-manager --no-build

# jupyterlab renderer support
jupyter labextension install jupyterlab-plotly --no-build

# FigureWidget support
jupyter labextension install plotlywidget --no-build

# Build extensions (must be done to activate extensions since --no-build is used above)
jupyter lab build

# Unset NODE_OPTIONS environment variable
# (OS X/Linux)
unset NODE_OPTIONS
# (Windows)
set NODE_OPTIONS=
```

# Download dataset

Run `scrap_design_seeds.ipynb`