from job_agent.matcher import filter_jobs


def test_no_filters_returns_everything(make_job):
    jobs = [make_job(id="1"), make_job(id="2")]
    assert filter_jobs(jobs) == jobs


def test_keyword_matches_title(make_job):
    jobs = [make_job(title="Rust Developer", tags=[], description="")]
    assert filter_jobs(jobs, keywords=["rust"]) == jobs


def test_keyword_matches_company(make_job):
    jobs = [make_job(company="Django Corp", title="", tags=[], description="")]
    assert filter_jobs(jobs, keywords=["django"]) == jobs


def test_keyword_matches_tags(make_job):
    jobs = [make_job(title="", company="", description="", tags=["golang"])]
    assert filter_jobs(jobs, keywords=["golang"]) == jobs


def test_keyword_matches_description_after_stripping_html(make_job):
    jobs = [make_job(title="", company="", tags=[], description="<p>Kubernetes</p>")]
    assert filter_jobs(jobs, keywords=["kubernetes"]) == jobs


def test_keyword_matching_is_case_insensitive(make_job):
    jobs = [make_job(title="PYTHON Engineer", tags=[], description="")]
    assert filter_jobs(jobs, keywords=["python"]) == jobs


def test_keywords_are_or_matched(make_job):
    # A job matching ANY keyword is kept.
    jobs = [make_job(title="Ruby dev", tags=[], description="")]
    assert filter_jobs(jobs, keywords=["python", "ruby"]) == jobs


def test_non_matching_keyword_excludes_job(make_job):
    jobs = [make_job(title="Ruby dev", company="", tags=[], description="")]
    assert filter_jobs(jobs, keywords=["python"]) == []


def test_location_substring_match(make_job):
    match = make_job(id="1", location="San Francisco, CA")
    other = make_job(id="2", location="Berlin, Germany")
    result = filter_jobs([match, other], location="san francisco")
    assert result == [match]


def test_location_is_case_insensitive(make_job):
    match = make_job(location="New York")
    assert filter_jobs([match], location="new york") == [match]


def test_location_remote_matches_all_jobs(make_job):
    # Intentional design: RemoteOK is a remote-only board where jobs may be
    # tagged "Worldwide"/"Europe"/empty, so filtering on "remote" keeps them all
    # rather than requiring the literal word "remote" in the location string.
    jobs = [
        make_job(id="1", location="Worldwide"),
        make_job(id="2", location="Europe"),
        make_job(id="3", location=""),
    ]
    assert filter_jobs(jobs, location="remote") == jobs


def test_keyword_and_location_combined(make_job):
    keep = make_job(id="1", title="Python dev", location="Austin", tags=[], description="")
    wrong_kw = make_job(id="2", title="Java dev", location="Austin", tags=[], description="")
    wrong_loc = make_job(id="3", title="Python dev", location="Boston", tags=[], description="")
    result = filter_jobs([keep, wrong_kw, wrong_loc], keywords=["python"], location="austin")
    assert result == [keep]
