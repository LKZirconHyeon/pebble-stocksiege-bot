def paginate_list(array: list, per_page: int = 10):
    out, page = [], []
    for x in array:
        page.append(x)
        if len(page) == per_page:
            out.append(page); page = []
    if page: out.append(page)
    return out
