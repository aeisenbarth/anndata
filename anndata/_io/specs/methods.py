from __future__ import annotations

from os import PathLike
from collections.abc import Mapping
from functools import partial
from typing import Union, Literal
from types import MappingProxyType
from warnings import warn

import h5py
import numpy as np
import pandas as pd
from scipy import sparse

import anndata as ad
from anndata import AnnData, Raw
from anndata._core.index import _normalize_indices
from anndata._core.merge import intersect_keys
from anndata._core.sparse_dataset import SparseDataset
from anndata._core import views
from anndata.compat import (
    OverloadedDict,
    ZarrArray,
    ZarrGroup,
    _read_attr,
    _from_fixed_length_strings,
    _decode_structured_array,
)
from anndata._io.utils import report_write_key_on_error, check_key, H5PY_V3
from anndata._warnings import OldFormatWarning

from .registry import (
    _REGISTRY,
    IOSpec,
    get_spec,
    read_elem,
    read_elem_partial,
    write_elem,
)

H5Array = h5py.Dataset
H5Group = h5py.Group


####################
# Dispatch methods #
####################

# def is_full_slice(idx):
#     if isinstance(idx, tuple)len(idx) == 1:

#     if isinstance(idx, type(None)):
#         return True
#     elif idx is Ellipsis:
#         return True
#     elif isinstance(idx, tuple):
#         for el in idx:
#             if isinstance(el, type(None)):
#                 pass
#             elif isinstance(el, slice):
#                 if el != slice(None):
#                     return False
#             else:
#                 return False
#         return True
#     return False


################################
# Fallbacks / backwards compat #
################################

# Note: there is no need for writing in a backwards compatible format, maybe


@_REGISTRY.register_read(H5Group, IOSpec("", ""))
@_REGISTRY.register_read(H5Array, IOSpec("", ""))
def read_basic(elem):
    from anndata._io import h5ad

    warn(
        f"Element '{elem.name}' was written without encoding metadata.",
        OldFormatWarning,
        stacklevel=3,
    )

    if isinstance(elem, Mapping):
        # Backwards compat sparse arrays
        if "h5sparse_format" in elem.attrs:
            return SparseDataset(elem).to_memory()
        return {k: read_elem(v) for k, v in elem.items()}
    elif isinstance(elem, h5py.Dataset):
        return h5ad.read_dataset(elem)  # TODO: Handle legacy


@_REGISTRY.register_read(ZarrGroup, IOSpec("", ""))
@_REGISTRY.register_read(ZarrArray, IOSpec("", ""))
def read_basic_zarr(elem):
    from anndata._io import zarr

    warn(
        f"Element '{elem.name}' was written without encoding metadata.",
        OldFormatWarning,
        stacklevel=3,
    )

    if isinstance(elem, Mapping):
        # Backwards compat sparse arrays
        if "h5sparse_format" in elem.attrs:
            return SparseDataset(elem).to_memory()
        return {k: read_elem(v) for k, v in elem.items()}
    elif isinstance(elem, ZarrArray):
        return zarr.read_dataset(elem)  # TODO: Handle legacy


# @_REGISTRY.register_read_partial(IOSpec("", ""))
# def read_basic_partial(elem, *, items=None, indices=(slice(None), slice(None))):
#     if isinstance(elem, Mapping):
#         return _read_partial(elem, items=items, indices=indices)
#     elif indices != (slice(None), slice(None)):
#         return elem[indices]
#     else:
#         return elem[()]


###########
# AnnData #
###########


def read_indices(group):
    obs_group = group["obs"]
    obs_idx_elem = obs_group[_read_attr(obs_group.attrs, "_index")]
    obs_idx = read_elem(obs_idx_elem)
    var_group = group["var"]
    var_idx_elem = var_group[_read_attr(var_group.attrs, "_index")]
    var_idx = read_elem(var_idx_elem)
    return obs_idx, var_idx


def read_partial(
    pth: PathLike,
    *,
    obs_idx=slice(None),
    var_idx=slice(None),
    X=True,
    obs=None,
    var=None,
    obsm=None,
    varm=None,
    obsp=None,
    varp=None,
    layers=None,
    uns=None,
) -> ad.AnnData:
    result = {}
    with h5py.File(pth, "r") as f:
        obs_idx, var_idx = _normalize_indices((obs_idx, var_idx), *read_indices(f))
        result["obs"] = read_elem_partial(
            f["obs"], items=obs, indices=(obs_idx, slice(None))
        )
        result["var"] = read_elem_partial(
            f["var"], items=var, indices=(var_idx, slice(None))
        )
        if X:
            result["X"] = read_elem_partial(f["X"], indices=(obs_idx, var_idx))
        else:
            result["X"] = sparse.csr_matrix((len(result["obs"]), len(result["var"])))
        if "obsm" in f:
            result["obsm"] = _read_partial(
                f["obsm"], items=obsm, indices=(obs_idx, slice(None))
            )
        if "varm" in f:
            result["varm"] = _read_partial(
                f["varm"], items=varm, indices=(var_idx, slice(None))
            )
        if "obsp" in f:
            result["obsp"] = _read_partial(
                f["obsp"], items=obsp, indices=(obs_idx, obs_idx)
            )
        if "varp" in f:
            result["varp"] = _read_partial(
                f["varp"], items=varp, indices=(var_idx, var_idx)
            )
        if "layers" in f:
            result["layers"] = _read_partial(
                f["layers"], items=layers, indices=(obs_idx, var_idx)
            )
        if "uns" in f:
            result["uns"] = _read_partial(f["uns"], items=uns)

    return ad.AnnData(**result)


def _read_partial(group, *, items=None, indices=(slice(None), slice(None))):
    if group is None:
        return None
    if items is None:
        keys = intersect_keys((group,))
    else:
        keys = intersect_keys((group, items))
    result = {}
    for k in keys:
        if isinstance(items, Mapping):
            next_items = items.get(k, None)
        else:
            next_items = None
        result[k] = read_elem_partial(group[k], items=next_items, indices=indices)
    return result


@_REGISTRY.register_write(ZarrGroup, AnnData, IOSpec("anndata", "0.1.0"))
@_REGISTRY.register_write(H5Group, AnnData, IOSpec("anndata", "0.1.0"))
def write_anndata(f, k, adata, dataset_kwargs=MappingProxyType({})):
    g = f.require_group(k)
    write_elem(g, "X", adata.X, dataset_kwargs=dataset_kwargs)
    write_elem(g, "obs", adata.obs, dataset_kwargs=dataset_kwargs)
    write_elem(g, "var", adata.var, dataset_kwargs=dataset_kwargs)
    write_elem(g, "obsm", dict(adata.obsm), dataset_kwargs=dataset_kwargs)
    write_elem(g, "varm", dict(adata.varm), dataset_kwargs=dataset_kwargs)
    write_elem(g, "obsp", dict(adata.obsp), dataset_kwargs=dataset_kwargs)
    write_elem(g, "varp", dict(adata.varp), dataset_kwargs=dataset_kwargs)
    write_elem(g, "layers", dict(adata.layers), dataset_kwargs=dataset_kwargs)
    write_elem(g, "uns", dict(adata.uns), dataset_kwargs=dataset_kwargs)
    write_elem(g, "raw", adata.raw, dataset_kwargs=dataset_kwargs)


@_REGISTRY.register_read(H5Group, IOSpec("anndata", "0.1.0"))
@_REGISTRY.register_read(H5Group, IOSpec("raw", "0.1.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("anndata", "0.1.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("raw", "0.1.0"))
def read_anndata(elem):
    d = {}
    for k in [
        "X",
        "obs",
        "var",
        "obsm",
        "varm",
        "obsp",
        "varp",
        "layers",
        "uns",
        "raw",
    ]:
        if k in elem:
            d[k] = read_elem(elem[k])
        if "X" in d:
            d["dtype"] = d["X"].dtype
    return AnnData(**d)


@_REGISTRY.register_write(H5Group, Raw, IOSpec("raw", "0.1.0"))
@_REGISTRY.register_write(ZarrGroup, Raw, IOSpec("raw", "0.1.0"))
def write_raw(f, k, raw, dataset_kwargs=MappingProxyType({})):
    g = f.create_group(k)
    write_elem(g, "X", raw.X, dataset_kwargs=dataset_kwargs)
    write_elem(g, "var", raw.var, dataset_kwargs=dataset_kwargs)
    write_elem(g, "varm", dict(raw.varm), dataset_kwargs=dataset_kwargs)


############
# Mappings #
############


@_REGISTRY.register_read(H5Group, IOSpec("dict", "0.1.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("dict", "0.1.0"))
def read_mapping(elem):
    return {k: read_elem(v) for k, v in elem.items()}


@_REGISTRY.register_write(H5Group, OverloadedDict, IOSpec("dict", "0.1.0"))
@_REGISTRY.register_write(H5Group, dict, IOSpec("dict", "0.1.0"))
@_REGISTRY.register_write(ZarrGroup, OverloadedDict, IOSpec("dict", "0.1.0"))
@_REGISTRY.register_write(ZarrGroup, dict, IOSpec("dict", "0.1.0"))
def write_mapping(f, k, v, dataset_kwargs=MappingProxyType({})):
    g = f.create_group(k)
    for sub_k, sub_v in v.items():
        write_elem(g, sub_k, sub_v, dataset_kwargs=dataset_kwargs)


##############
# np.ndarray #
##############


@_REGISTRY.register_write(H5Group, list, IOSpec("array", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, list, IOSpec("array", "0.2.0"))
def write_list(f, k, elem, dataset_kwargs=MappingProxyType({})):
    write_elem(f, k, np.array(elem), dataset_kwargs=dataset_kwargs)


# TODO: Is this the right behaviour for MaskedArrays?
# It's in the `AnnData.concatenate` docstring, but should we keep it?
@_REGISTRY.register_write(H5Group, views.ArrayView, IOSpec("array", "0.2.0"))
@_REGISTRY.register_write(H5Group, np.ndarray, IOSpec("array", "0.2.0"))
@_REGISTRY.register_write(H5Group, h5py.Dataset, IOSpec("array", "0.2.0"))
@_REGISTRY.register_write(H5Group, np.ma.MaskedArray, IOSpec("array", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, views.ArrayView, IOSpec("array", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, np.ndarray, IOSpec("array", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, h5py.Dataset, IOSpec("array", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, np.ma.MaskedArray, IOSpec("array", "0.2.0"))
def write_basic(f, k, elem, dataset_kwargs=MappingProxyType({})):
    """Write methods which underlying library handles nativley."""
    f.create_dataset(k, data=elem, **dataset_kwargs)


@_REGISTRY.register_read(H5Array, IOSpec("array", "0.2.0"))
@_REGISTRY.register_read(ZarrArray, IOSpec("array", "0.2.0"))
@_REGISTRY.register_read(ZarrArray, IOSpec("string-array", "0.2.0"))
def read_array(elem):
    return elem[()]


@_REGISTRY.register_read_partial(H5Array, IOSpec("array", "0.2.0"))
@_REGISTRY.register_read_partial(ZarrArray, IOSpec("array", "0.2.0"))
@_REGISTRY.register_read_partial(ZarrArray, IOSpec("string-array", "0.2.0"))
def read_array_partial(elem, *, items=None, indices=(slice(None, None))):
    return elem[indices]


# arrays of strings
@_REGISTRY.register_read(H5Array, IOSpec("string-array", "0.2.0"))
def read_string_array(d):
    return read_array(d.asstr())


@_REGISTRY.register_read_partial(H5Array, IOSpec("string-array", "0.2.0"))
def read_array_partial(d, items=None, indices=slice(None)):
    return read_array_partial(d.asstr(), items=items, indices=indices)


@_REGISTRY.register_write(
    H5Group, (views.ArrayView, "U"), IOSpec("string-array", "0.2.0")
)
@_REGISTRY.register_write(
    H5Group, (views.ArrayView, "O"), IOSpec("string-array", "0.2.0")
)
@_REGISTRY.register_write(H5Group, (np.ndarray, "U"), IOSpec("string-array", "0.2.0"))
@_REGISTRY.register_write(H5Group, (np.ndarray, "O"), IOSpec("string-array", "0.2.0"))
def write_vlen_string_array(f, k, elem, dataset_kwargs=MappingProxyType({})):
    """Write methods which underlying library handles nativley."""
    str_dtype = h5py.special_dtype(vlen=str)
    f.create_dataset(k, data=elem.astype(str_dtype), dtype=str_dtype, **dataset_kwargs)


@_REGISTRY.register_write(
    ZarrGroup, (views.ArrayView, "U"), IOSpec("string-array", "0.2.0")
)
@_REGISTRY.register_write(
    ZarrGroup, (views.ArrayView, "O"), IOSpec("string-array", "0.2.0")
)
@_REGISTRY.register_write(ZarrGroup, (np.ndarray, "U"), IOSpec("string-array", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, (np.ndarray, "O"), IOSpec("string-array", "0.2.0"))
def write_vlen_string_array_zarr(f, k, elem, dataset_kwargs=MappingProxyType({})):
    import numcodecs

    f.create_dataset(
        k,
        shape=elem.shape,
        dtype=object,
        object_codec=numcodecs.VLenUTF8(),
        **dataset_kwargs,
    )
    f[k][:] = elem


###############
# np.recarray #
###############


def _to_hdf5_vlen_strings(value: np.ndarray) -> np.ndarray:
    """This corrects compound dtypes to work with hdf5 files."""
    new_dtype = []
    for dt_name, (dt_type, _) in value.dtype.fields.items():
        if dt_type.kind in ("U", "O"):
            new_dtype.append((dt_name, h5py.special_dtype(vlen=str)))
        else:
            new_dtype.append((dt_name, dt_type))
    return value.astype(new_dtype)


@_REGISTRY.register_read(H5Array, IOSpec("rec-array", "0.2.0"))
@_REGISTRY.register_read(ZarrArray, IOSpec("rec-array", "0.2.0"))
def read_recarray(d):
    value = d[()]
    dtype = value.dtype
    value = _from_fixed_length_strings(value)
    if H5PY_V3:
        value = _decode_structured_array(value, dtype=dtype)
    return value


@_REGISTRY.register_write(H5Group, (np.ndarray, "V"), IOSpec("rec-array", "0.2.0"))
@_REGISTRY.register_write(H5Group, np.recarray, IOSpec("rec-array", "0.2.0"))
def write_recarray(f, k, elem, dataset_kwargs=MappingProxyType({})):
    f.create_dataset(k, data=_to_hdf5_vlen_strings(elem), **dataset_kwargs)


@_REGISTRY.register_write(ZarrGroup, (np.ndarray, "V"), IOSpec("rec-array", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, np.recarray, IOSpec("rec-array", "0.2.0"))
def write_recarray_zarr(f, k, elem, dataset_kwargs=MappingProxyType({})):
    from anndata.compat import _to_fixed_length_strings

    f.create_dataset(k, data=_to_fixed_length_strings(elem), **dataset_kwargs)


#################
# Sparse arrays #
#################


def write_sparse_compressed(
    f, key, value, fmt: Literal["csr", "csc"], dataset_kwargs=MappingProxyType({})
):
    g = f.create_group(key)
    g.attrs["shape"] = value.shape

    # Allow resizing
    if "maxshape" not in dataset_kwargs:
        dataset_kwargs = dict(maxshape=(None,), **dataset_kwargs)

    g.create_dataset("data", data=value.data, **dataset_kwargs)
    g.create_dataset("indices", data=value.indices, **dataset_kwargs)
    g.create_dataset("indptr", data=value.indptr, **dataset_kwargs)


write_csr = partial(write_sparse_compressed, fmt="csr")
write_csc = partial(write_sparse_compressed, fmt="csc")
_REGISTRY.register_write(H5Group, sparse.csr_matrix, IOSpec("csr_matrix", "0.1.0"))(
    write_csr
)
_REGISTRY.register_write(H5Group, views.SparseCSRView, IOSpec("csr_matrix", "0.1.0"))(
    write_csr
)
_REGISTRY.register_write(H5Group, sparse.csc_matrix, IOSpec("csc_matrix", "0.1.0"))(
    write_csc
)
_REGISTRY.register_write(H5Group, views.SparseCSCView, IOSpec("csc_matrix", "0.1.0"))(
    write_csc
)
_REGISTRY.register_write(ZarrGroup, sparse.csr_matrix, IOSpec("csr_matrix", "0.1.0"))(
    write_csr
)
_REGISTRY.register_write(ZarrGroup, views.SparseCSRView, IOSpec("csr_matrix", "0.1.0"))(
    write_csr
)
_REGISTRY.register_write(ZarrGroup, sparse.csc_matrix, IOSpec("csc_matrix", "0.1.0"))(
    write_csc
)
_REGISTRY.register_write(ZarrGroup, views.SparseCSCView, IOSpec("csc_matrix", "0.1.0"))(
    write_csc
)


@_REGISTRY.register_write(H5Group, SparseDataset, IOSpec("", "0.1.0"))
@_REGISTRY.register_write(ZarrGroup, SparseDataset, IOSpec("", "0.1.0"))
def write_sparse_dataset(f, k, elem, dataset_kwargs=MappingProxyType({})):
    write_sparse_compressed(
        f, k, elem.to_backed(), fmt=elem.format_str, dataset_kwargs=dataset_kwargs
    )
    # TODO: Cleaner way to do this
    f[k].attrs["encoding-type"] = f"{elem.format_str}_matrix"
    f[k].attrs["encoding-version"] = "0.1.0"


@_REGISTRY.register_read(H5Group, IOSpec("csc_matrix", "0.1.0"))
@_REGISTRY.register_read(H5Group, IOSpec("csr_matrix", "0.1.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("csc_matrix", "0.1.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("csr_matrix", "0.1.0"))
def read_sparse(elem):
    return SparseDataset(elem).to_memory()


@_REGISTRY.register_read_partial(H5Group, IOSpec("csc_matrix", "0.1.0"))
@_REGISTRY.register_read_partial(H5Group, IOSpec("csr_matrix", "0.1.0"))
def read_sparse_partial(elem, *, items=None, indices=(slice(None), slice(None))):
    return SparseDataset(elem)[indices]


##############
# DataFrames #
##############


@_REGISTRY.register_write(H5Group, views.DataFrameView, IOSpec("dataframe", "0.2.0"))
@_REGISTRY.register_write(H5Group, pd.DataFrame, IOSpec("dataframe", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, views.DataFrameView, IOSpec("dataframe", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, pd.DataFrame, IOSpec("dataframe", "0.2.0"))
def write_dataframe(f, key, df, dataset_kwargs=MappingProxyType({})):
    # Check arguments
    for reserved in ("_index",):
        if reserved in df.columns:
            raise ValueError(f"{reserved!r} is a reserved name for dataframe columns.")
    group = f.create_group(key)
    col_names = [check_key(c) for c in df.columns]
    group.attrs["column-order"] = col_names

    if df.index.name is not None:
        index_name = df.index.name
    else:
        index_name = "_index"
    group.attrs["_index"] = check_key(index_name)

    # ._values is "the best" array representation. It's the true array backing the
    # object, where `.values` is always a np.ndarray and .array is always a pandas
    # array.
    write_elem(group, index_name, df.index._values, dataset_kwargs=dataset_kwargs)
    for colname, series in df.items():
        # TODO: this should write the "true" representation of the series (i.e. the underlying array or ndarray depending)
        write_elem(group, colname, series._values, dataset_kwargs=dataset_kwargs)


@_REGISTRY.register_read(H5Group, IOSpec("dataframe", "0.2.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("dataframe", "0.2.0"))
def read_dataframe(elem):
    columns = list(_read_attr(elem.attrs, "column-order"))
    idx_key = _read_attr(elem.attrs, "_index")
    df = pd.DataFrame(
        {k: read_elem(elem[k]) for k in columns},
        index=read_elem(elem[idx_key]),
        columns=list(columns),
    )
    if idx_key != "_index":
        df.index.name = idx_key
    return df


# TODO: Figure out what indices is allowed to be at each element
@_REGISTRY.register_read_partial(H5Group, IOSpec("dataframe", "0.2.0"))
@_REGISTRY.register_read_partial(ZarrGroup, IOSpec("dataframe", "0.2.0"))
def read_dataframe_partial(
    elem, *, items=None, indices=(slice(None, None), slice(None, None))
):
    if items is not None:
        columns = [
            col for col in _read_attr(elem.attrs, "column-order") if col in items
        ]
    else:
        columns = list(_read_attr(elem.attrs, "column-order"))
    idx_key = _read_attr(elem.attrs, "_index")
    df = pd.DataFrame(
        {k: read_elem_partial(elem[k], indices=indices[0]) for k in columns},
        index=read_elem_partial(elem[idx_key], indices=indices[0]),
        columns=list(columns),
    )
    if idx_key != "_index":
        df.index.name = idx_key
    return df


# Backwards compat dataframe reading


@_REGISTRY.register_read(H5Group, IOSpec("dataframe", "0.1.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("dataframe", "0.1.0"))
def read_dataframe_0_1_0(elem):
    columns = _read_attr(elem.attrs, "column-order")
    idx_key = _read_attr(elem.attrs, "_index")
    df = pd.DataFrame(
        {k: read_series(elem[k]) for k in columns},
        index=read_series(elem[idx_key]),
        columns=list(columns),
    )
    if idx_key != "_index":
        df.index.name = idx_key
    return df


def read_series(dataset: h5py.Dataset) -> Union[np.ndarray, pd.Categorical]:
    # For reading older dataframes
    if "categories" in dataset.attrs:
        if isinstance(dataset, ZarrArray):
            import zarr

            parent_name = dataset.name.rstrip(dataset.basename)
            parent = zarr.open(dataset.store)[parent_name]
        else:
            parent = dataset.parent
        categories_dset = parent[_read_attr(dataset.attrs, "categories")]
        categories = read_elem(categories_dset)
        ordered = bool(_read_attr(categories_dset.attrs, "ordered", False))
        return pd.Categorical.from_codes(
            read_elem(dataset), categories, ordered=ordered
        )
    else:
        return read_elem(dataset)


@_REGISTRY.register_read_partial(H5Group, IOSpec("dataframe", "0.1.0"))
@_REGISTRY.register_read_partial(ZarrGroup, IOSpec("dataframe", "0.1.0"))
def read_partial_dataframe_0_1_0(
    elem, *, items=None, indices=(slice(None), slice(None))
):
    if items is None:
        items = slice(None)
    else:
        items = list(items)
    return read_elem(elem)[items].iloc[indices[0]]


###############
# Categorical #
###############


@_REGISTRY.register_write(H5Group, pd.Categorical, IOSpec("categorical", "0.2.0"))
@_REGISTRY.register_write(ZarrGroup, pd.Categorical, IOSpec("categorical", "0.2.0"))
def write_categorical(f, k, v, dataset_kwargs=MappingProxyType({})):
    g = f.create_group(k)
    g.attrs["ordered"] = bool(v.ordered)

    write_elem(g, "codes", v.codes, dataset_kwargs=dataset_kwargs)
    write_elem(g, "categories", v.categories._values, dataset_kwargs=dataset_kwargs)


@_REGISTRY.register_read(H5Group, IOSpec("categorical", "0.2.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("categorical", "0.2.0"))
def read_categorical(elem):
    return pd.Categorical.from_codes(
        codes=read_elem(elem["codes"]),
        categories=read_elem(elem["categories"]),
        ordered=_read_attr(elem.attrs, "ordered"),
    )


@_REGISTRY.register_read_partial(H5Group, IOSpec("categorical", "0.2.0"))
@_REGISTRY.register_read_partial(ZarrGroup, IOSpec("categorical", "0.2.0"))
def read_categorical(elem, *, items=None, indices=(slice(None),)):
    return pd.Categorical.from_codes(
        codes=read_elem_partial(elem["codes"], indices=indices),
        categories=read_elem(elem["categories"]),
        ordered=_read_attr(elem.attrs, "ordered"),
    )


####################
# Pandas nullables #
####################


@_REGISTRY.register_write(
    H5Group, pd.arrays.IntegerArray, IOSpec("nullable-integer", "0.1.0")
)
@_REGISTRY.register_write(
    ZarrGroup, pd.arrays.IntegerArray, IOSpec("nullable-integer", "0.1.0")
)
@_REGISTRY.register_write(
    H5Group, pd.arrays.BooleanArray, IOSpec("nullable-boolean", "0.1.0")
)
@_REGISTRY.register_write(
    ZarrGroup, pd.arrays.BooleanArray, IOSpec("nullable-boolean", "0.1.0")
)
def write_nullable_integer(f, k, v, dataset_kwargs=MappingProxyType({})):
    g = f.create_group(k)
    if v._mask is not None:
        write_elem(g, "mask", v._mask, dataset_kwargs=dataset_kwargs)
    write_elem(g, "values", v._data, dataset_kwargs=dataset_kwargs)


@_REGISTRY.register_read(H5Group, IOSpec("nullable-integer", "0.1.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("nullable-integer", "0.1.0"))
def read_nullable_integer(elem):
    if "mask" in elem:
        return pd.arrays.IntegerArray(
            read_elem(elem["values"]), mask=read_elem(elem["mask"])
        )
    else:
        return pd.array(read_elem(elem["values"]))


@_REGISTRY.register_read(H5Group, IOSpec("nullable-boolean", "0.1.0"))
@_REGISTRY.register_read(ZarrGroup, IOSpec("nullable-boolean", "0.1.0"))
def read_nullable_boolean(elem):
    if "mask" in elem:
        return pd.arrays.BooleanArray(
            read_elem(elem["values"]), mask=read_elem(elem["mask"])
        )
    else:
        return pd.array(read_elem(elem["values"]))


###########
# Scalars #
###########


@_REGISTRY.register_read(H5Array, IOSpec("numeric-scalar", "0.2.0"))
@_REGISTRY.register_read(ZarrArray, IOSpec("numeric-scalar", "0.2.0"))
def read_scalar(elem):
    return elem[()]


def write_scalar(f, key, value, dataset_kwargs=MappingProxyType({})):
    return f.create_dataset(key, data=np.array(value), **dataset_kwargs)


def write_hdf5_scalar(f, key, value, dataset_kwargs=MappingProxyType({})):
    # Can’t compress scalars, error is thrown
    dataset_kwargs = dataset_kwargs.copy()
    dataset_kwargs.pop("compression", None)
    dataset_kwargs.pop("compression_opts", None)
    f.create_dataset(key, data=np.array(value), **dataset_kwargs)


# fmt: off
for numeric_scalar_type in [
    bool, np.bool_,
    np.uint8, np.uint16, np.uint32, np.uint64,
    int, np.int8, np.int16, np.int32, np.int64,
    float, *np.floating.__subclasses__(),
    *np.complexfloating.__subclasses__(),
]:
    _REGISTRY.register_write(H5Group, numeric_scalar_type, IOSpec("numeric-scalar", "0.2.0"))(write_hdf5_scalar)
    _REGISTRY.register_write(ZarrGroup, numeric_scalar_type, IOSpec("numeric-scalar", "0.2.0"))(write_scalar)
# fmt: on

_REGISTRY.register_write(ZarrGroup, str, IOSpec("string", "0.2.0"))(write_scalar)
_REGISTRY.register_write(ZarrGroup, np.str_, IOSpec("string", "0.2.0"))(write_scalar)


@_REGISTRY.register_read(H5Array, IOSpec("string", "0.2.0"))
def read_hdf5_string(elem):
    return elem.asstr()[()]


@_REGISTRY.register_read(ZarrArray, IOSpec("string", "0.2.0"))
def read_zarr_string(elem):
    return str(elem[()])


_REGISTRY.register_read(H5Array, IOSpec("bytes", "0.2.0"))(read_scalar)
_REGISTRY.register_read(ZarrArray, IOSpec("bytes", "0.2.0"))(read_scalar)


@_REGISTRY.register_write(H5Group, np.str_, IOSpec("string", "0.2.0"))
@_REGISTRY.register_write(H5Group, str, IOSpec("string", "0.2.0"))
def write_string(f, k, v, dataset_kwargs):
    dataset_kwargs = dataset_kwargs.copy()
    dataset_kwargs.pop("compression", None)
    dataset_kwargs.pop("compression_opts", None)
    f.create_dataset(
        k, data=np.array(v, dtype=h5py.string_dtype(encoding="utf-8")), **dataset_kwargs
    )


# @_REGISTRY.register_write(np.bytes_, IOSpec("bytes", "0.2.0"))
# @_REGISTRY.register_write(bytes, IOSpec("bytes", "0.2.0"))
# def write_string(f, k, v, dataset_kwargs):
#     if "compression" in dataset_kwargs:
#         dataset_kwargs = dict(dataset_kwargs)
#         dataset_kwargs.pop("compression")
#     f.create_dataset(k, data=np.array(v), **dataset_kwargs)