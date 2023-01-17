import click
import pandas as pd
import pysam
from freyja.convert_paths2barcodes import parse_tree_paths,\
    convert_to_barcodes, reversion_checking, check_mutation_chain
from freyja.sample_deconv import buildLineageMap, build_mix_and_depth_arrays,\
    reindex_dfs, map_to_constellation, solve_demixing_problem,\
    perform_bootstrap
from freyja.updates import download_tree, convert_tree,\
    get_curated_lineage_data, get_cl_lineages,\
    download_barcodes, download_barcodes_wgisaid
from freyja.utils import agg, makePlot_simple, makePlot_time,\
    make_dashboard, checkConfig, get_abundance, calc_rel_growth_rates
import os
import glob
import re
import subprocess
import sys
import yaml

locDir = os.path.abspath(os.path.join(os.path.realpath(__file__), os.pardir))


@click.group()
@click.version_option('1.3.12')
def cli():
    pass


@cli.command()
@click.argument('variants', type=click.Path(exists=True))
@click.argument('depths', type=click.Path(exists=True))
@click.option('--eps', default=1e-3, help='minimum abundance to include')
@click.option('--barcodes', default='-1', help='custom barcode file')
@click.option('--meta', default='-1', help='custom lineage metadata file')
@click.option('--output', default='demixing_result.csv', help='Output file',
              type=click.Path(exists=False))
@click.option('--covcut', default=10, help='depth cutoff for\
                                            coverage estimate')
@click.option('--confirmedonly', is_flag=True, default=False)
@click.option('--wgisaid', is_flag=True, default=False,
              help='larger library with non-public lineages')
def demix(variants, depths, output, eps, barcodes, meta,
          covcut, confirmedonly, wgisaid):
    locDir = os.path.abspath(os.path.join(os.path.realpath(__file__),
                             os.pardir))
    # option for custom barcodes
    if barcodes != '-1':
        df_barcodes = pd.read_csv(barcodes, index_col=0)
    else:
        if not wgisaid:
            df_barcodes = pd.read_csv(os.path.join(locDir,
                                      'data/usher_barcodes.csv'), index_col=0)
        else:
            df_barcodes = pd.read_csv(os.path.join(locDir,
                                      'data/usher_barcodes_with_gisaid.csv'),
                                      index_col=0)
    if confirmedonly:
        confirmed = [dfi for dfi in df_barcodes.index
                     if 'proposed' not in dfi and 'misc' not in dfi]
        df_barcodes = df_barcodes.loc[confirmed, :]

    # drop intra-lineage diversity naming (keeps separate barcodes)
    indexSimplified = [dfi.split('_')[0] for dfi in df_barcodes.index]
    df_barcodes = df_barcodes.loc[indexSimplified, :]

    muts = list(df_barcodes.columns)
    mapDict = buildLineageMap(meta)
    print('building mix/depth matrices')
    # assemble data from (possibly) mixed samples
    mix, depths_, cov = build_mix_and_depth_arrays(variants, depths, muts,
                                                   covcut)
    print('demixing')
    df_barcodes, mix, depths_ = reindex_dfs(df_barcodes, mix, depths_)
    sample_strains, abundances, error = solve_demixing_problem(df_barcodes,
                                                               mix,
                                                               depths_,
                                                               eps)
    # merge intra-lineage diversity if multiple hits.
    if len(set(sample_strains)) < len(sample_strains):
        localDict = {}
        for jj, lin in enumerate(sample_strains):
            if lin not in localDict.keys():
                localDict[lin] = abundances[jj]
            else:
                localDict[lin] += abundances[jj]
        # ensure descending order
        localDict = dict(sorted(localDict.items(),
                                key=lambda x: x[1],
                                reverse=True))
        sample_strains = list(localDict.keys())
        abundances = list(localDict.values())

    localDict = map_to_constellation(sample_strains, abundances, mapDict)
    # assemble into series and write.
    sols_df = pd.Series(data=(localDict, sample_strains, abundances,
                              error, cov),
                        index=['summarized', 'lineages',
                        'abundances', 'resid', 'coverage'],
                        name=mix.name)
    # convert lineage/abundance readouts to single line strings
    sols_df['lineages'] = ' '.join(sols_df['lineages'])
    sols_df['abundances'] = ['%.8f' % ab for ab in sols_df['abundances']]
    sols_df['abundances'] = ' '.join(sols_df['abundances'])
    sols_df.to_csv(output, sep='\t')


@cli.command()
@click.option('--outdir', default='-1',
              help='Output directory save updated files')
@click.option('--noncl', is_flag=True, default=True,
              help='only include lineages in cov-lineages')
@click.option('--wgisaid', is_flag=True, default=False,
              help='Larger library with non-public lineages')
@click.option('--buildlocal', is_flag=True, default=False,
              help='Perform barcode building locally')
def update(outdir, noncl, wgisaid, buildlocal):
    locDir = os.path.abspath(os.path.join(os.path.realpath(__file__),
                                          os.pardir))
    if outdir != '-1':
        # User specified directory
        locDir = outdir
    else:
        locDir = os.path.join(locDir, 'data')

    print('Getting outbreak data')
    get_curated_lineage_data(locDir)
    get_cl_lineages(locDir)
    # # get data from UShER
    if buildlocal:
        print('Downloading a new global tree')
        download_tree(locDir)
        print("Converting tree info to barcodes")
        convert_tree(locDir)  # returns paths for each lineage
        # Now parse into barcode form
        lineagePath = os.path.join(os.curdir, "lineagePaths.txt")
        print('Building barcodes from global phylogenetic tree')
        df = pd.read_csv(lineagePath, sep='\t')
        df = parse_tree_paths(df)
        df_barcodes = convert_to_barcodes(df)
        df_barcodes = reversion_checking(df_barcodes)
        df_barcodes = check_mutation_chain(df_barcodes)

        # as default:
        # since usher tree can be ahead of cov-lineages,
        # we drop lineages not in cov-lineages
        if noncl:
            # read linages.yml file
            with open(os.path.join(locDir, 'lineages.yml'), 'r') as f:
                try:
                    lineages_yml = yaml.safe_load(f)
                except yaml.YAMLError as exc:
                    raise ValueError('Error in lineages.yml file: ' + str(exc))
            lineageNames = [lineage['name'] for lineage in lineages_yml]
            df_barcodes = df_barcodes.loc[df_barcodes.index.isin(lineageNames)]
        else:
            print("Including lineages not yet in cov-lineages.")
        df_barcodes.to_csv(os.path.join(locDir, 'usher_barcodes.csv'))
        # delete files generated along the way that aren't needed anymore
        print('Cleaning up')
        os.remove(lineagePath)
        os.remove(os.path.join(locDir, "public-latest.all.masked.pb.gz"))
    elif not wgisaid:
        print('Downloading barcodes')
        download_barcodes(locDir)
    else:
        print('Downloading barcodes with GISAID-only (non-public) lineages')
        download_barcodes_wgisaid(locDir)


@cli.command()
@click.argument('bamfile', type=click.Path(exists=True))
@click.option('--ref', help='Reference',
              default=os.path.join(locDir,
                                   'data/NC_045512_Hu-1.fasta'),
              type=click.Path())
@click.option('--variants', help='Variant call output file', type=click.Path())
@click.option('--depths', help='Sequencing depth output file',
              type=click.Path())
@click.option('--refname', help='Ref name (for bams with multiple sequences)',
              default='')
@click.option('--minq', help='Minimum base quality score',
              default=20)
def variants(bamfile, ref, variants, depths, refname, minq):
    if len(refname) == 0:
        bashCmd = f"samtools mpileup -aa -A -d 600000 -Q {minq} -q 0 -B -f "\
                  f"{ref} {bamfile} | tee >(cut -f1-4 > {depths}) |"\
                  f" ivar variants -p {variants} -q {minq} -t 0.0 -r {ref}"
    else:
        bashCmd = f"samtools mpileup -aa -A -d 600000 -Q {minq} -q 0 -B -f "\
                  f"{ref} {bamfile} -r {refname} | tee >(cut -f1-4 > {depths}"\
                  f") | ivar variants -p {variants} -q {minq} -t 0.0 -r {ref}"
    sys.stdout.flush()  # force python to flush
    completed = subprocess.run(bashCmd, shell=True, executable="/bin/bash",
                               stdout=subprocess.PIPE)
    sys.exit(completed.returncode)


@cli.command()
@click.argument('variants', type=click.Path(exists=True))
@click.argument('depths', type=click.Path(exists=True))
@click.option('--nb', default=100, help='number of bootstraps')
@click.option('--nt', default=1, help='max number of cpus to use')
@click.option('--eps', default=1e-3, help='minimum abundance to include')
@click.option('--barcodes', default='-1', help='custom barcode file')
@click.option('--meta', default='-1', help='custom lineage metadata file')
@click.option('--output_base', default='test', help='Output file basename',
              type=click.Path(exists=False))
@click.option('--boxplot', default='',
              help='file format of boxplot output (e.g. pdf or png)')
@click.option('--confirmedonly', is_flag=True, default=False)
@click.option('--wgisaid', is_flag=True, default=False,
              help='larger library with non-public lineages')
def boot(variants, depths, output_base, eps, barcodes, meta,
         nb, nt, boxplot, confirmedonly, wgisaid):
    locDir = os.path.abspath(os.path.join(os.path.realpath(__file__),
                             os.pardir))
    # option for custom barcodes
    if barcodes != '-1':
        df_barcodes = pd.read_csv(barcodes, index_col=0)
    else:
        if not wgisaid:
            df_barcodes = pd.read_csv(os.path.join(locDir,
                                      'data/usher_barcodes.csv'), index_col=0)
        else:
            df_barcodes = pd.read_csv(os.path.join(locDir,
                                      'data/usher_barcodes_with_gisaid.csv'),
                                      index_col=0)
    if confirmedonly:
        confirmed = [dfi for dfi in df_barcodes.index
                     if 'proposed' not in dfi and 'misc' not in dfi]
        df_barcodes = df_barcodes.loc[confirmed, :]

    # drop intra-lineage diversity naming (keeps separate barcodes)
    indexSimplified = [dfi.split('_')[0] for dfi in df_barcodes.index]
    df_barcodes = df_barcodes.loc[indexSimplified, :]

    muts = list(df_barcodes.columns)
    mapDict = buildLineageMap(meta)
    print('building mix/depth matrices')
    # assemble data from (possibly) mixed samples
    covcut = 10  # set value, coverage estimate not returned to user from boot
    mix, depths_, cov = build_mix_and_depth_arrays(variants, depths, muts,
                                                   covcut)
    print('demixing')
    df_barcodes, mix, depths_ = reindex_dfs(df_barcodes, mix, depths_)
    lin_out, constell_out = perform_bootstrap(df_barcodes, mix, depths_,
                                              nb, eps, nt, mapDict, muts,
                                              boxplot, output_base)
    lin_out.to_csv(output_base + '_lineages.csv')
    constell_out.to_csv(output_base + '_summarized.csv')


@cli.command()
@click.argument('results', type=click.Path(exists=True))
@click.option('--ext', default='-1', help='file extension option')
@click.option('--output', default='aggregated_result.tsv', help='Output file',
              type=click.Path(exists=False))
def aggregate(results, ext, output):
    if ext != '-1':
        results_ = [fn for fn in glob.glob(results + '*' + ext)]
    else:
        results_ = [results + fn for fn in os.listdir(results)]
    df_demix = agg(results_)
    df_demix.to_csv(output, sep='\t')


@cli.command()
@click.argument('agg_results', type=click.Path(exists=True))
@click.option('--lineages', is_flag=True)
@click.option('--times', default='-1')
@click.option('--interval', default='MS')
@click.option('--colors', default='', help='path to csv of hex codes')
@click.option('--mincov', default=60., help='min genome coverage included')
@click.option('--output', default='mix_plot.pdf', help='Output file')
@click.option('--windowsize', default=14)
def plot(agg_results, lineages, times, interval, output, windowsize,
         colors, mincov):
    agg_df = pd.read_csv(agg_results, skipinitialspace=True, sep='\t',
                         index_col=0)
    # drop poor quality samples
    if 'coverage' in agg_df.columns:
        agg_df = agg_df[agg_df['coverage'] > mincov]
    else:
        print('WARNING: Freyja should be updated \
to include coverage estimates.')
    agg_df['abundances'] = agg_df['abundances'].astype(str)
    agg_df['summarized'] = agg_df['summarized'].astype(str)
    agg_df = agg_df[agg_df['summarized'] != '[]']
    if len(colors) > 0:
        colors0 = pd.read_csv(colors, header=None).values[0]
    else:
        colors0 = colors
    if times == '-1':
        # make basic plot, without time info
        makePlot_simple(agg_df, lineages, output, colors0)
    else:
        # make time aware plot
        times_df = pd.read_csv(times, skipinitialspace=True,
                               index_col=0)
        times_df['sample_collection_datetime'] = \
            pd.to_datetime(times_df['sample_collection_datetime'])
        makePlot_time(agg_df, lineages, times_df, interval, output,
                      windowsize, colors0)


@cli.command()
@click.argument('agg_results', type=click.Path(exists=True))
@click.argument('metadata', type=click.Path(exists=True))
@click.argument('title', type=click.Path(exists=True))
@click.argument('intro', type=click.Path(exists=True))
@click.option('--thresh', default=0.5, help='min lineage abundance included')
@click.option('--headerColor', default='#2fdcf5', help='color of header')
@click.option('--bodyColor', default='#ffffff', help='color of body')
@click.option('--scale_by_viral_load', is_flag=True,
              help='scale by viral load')
@click.option('--nboots', default=1000, help='Number of Bootstrap iterations')
@click.option('--serial_interval', default=5.5, help='Serial Interval')
@click.option('--config', default=None, help='path to yaml file')
@click.option('--mincov', default=60., help='min genome coverage included')
@click.option('--output', default='mydashboard.html', help='Output html file')
@click.option('--days', default=56, help='N Days used for growth calc')
@click.option('--grthresh', default=0.05, help='min avg prev. for growth')
@click.argument('hierarchy', type=click.Path(),
                default=os.path.join(locDir, 'data/lineages.yml'))
def dash(agg_results, metadata, title, intro, thresh, headercolor, bodycolor,
         scale_by_viral_load, nboots, serial_interval, config, mincov, output,
         days, grthresh, hierarchy):
    agg_df = pd.read_csv(agg_results, skipinitialspace=True, sep='\t',
                         index_col=0)
    # drop poor quality samples
    if 'coverage' in agg_df.columns:
        agg_df = agg_df[agg_df['coverage'] > mincov]
    else:
        print('WARNING: Freyja should be updated ' +
              'to include coverage estimates.')
    agg_df = agg_df[agg_df['summarized'] != '[]']

    meta_df = pd.read_csv(metadata, index_col=0)
    meta_df['sample_collection_datetime'] = \
        pd.to_datetime(meta_df['sample_collection_datetime'])
    # read in inputs
    with open(title, "r") as f:
        titleText = ''.join(f.readlines())
    with open(intro, "r") as f:
        introText = ''.join(f.readlines())
    if config is not None:
        with open(config, "r") as f:
            try:
                config = yaml.safe_load(f)
            except yaml.YAMLError as exc:
                raise ValueError('Error in config file: ' + str(exc))

    with open(hierarchy, 'r') as f:
        try:
            lineages_yml = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError('lineages.yml error, run update: ' + str(exc))

    # converts lineages_yml to a dictionary where the lineage names are the
    # keys.
    lineage_info = {}
    for lineage in lineages_yml:
        lineage_info[lineage['name']] = {'name': lineage['name'],
                                         'children': lineage['children']}
    if config is not None:
        config = checkConfig(config)
    else:
        config = {}
    make_dashboard(agg_df, meta_df, thresh, titleText, introText,
                   output, headercolor, bodycolor, scale_by_viral_load, config,
                   lineage_info, nboots, serial_interval, days, grthresh)


@cli.command()
@click.argument('agg_results', type=click.Path(exists=True))
@click.argument('metadata', type=click.Path(exists=True))
@click.option('--thresh', default=0.5, help='min lineage abundance in plot')
@click.option('--scale_by_viral_load', is_flag=True,
              help='scale by viral load')
@click.option('--nboots', default=1000, help='Number of Bootstrap iterations')
@click.option('--serial_interval', default=5.5, help='Serial Interval')
@click.option('--config', default=None, help='path to yaml file')
@click.option('--mincov', default=60., help='min genome coverage included')
@click.option('--output', default='rel_growth_rates.csv',
              help='Output html file')
@click.option('--days', default=56, help='N Days used for growth calc')
@click.option('--grthresh', default=0.05, help='min avg prev. for growth')
def relgrowthrate(agg_results, metadata, thresh, scale_by_viral_load, nboots,
                  serial_interval, config, mincov, output, days, grthresh):
    agg_df = pd.read_csv(agg_results, skipinitialspace=True, sep='\t',
                         index_col=0)
    # drop poor quality samples
    if 'coverage' in agg_df.columns:
        agg_df = agg_df[agg_df['coverage'] > mincov]
    else:
        print('WARNING: Freyja should be updated ' +
              'to include coverage estimates.')
    agg_df = agg_df[agg_df['summarized'] != '[]']

    meta_df = pd.read_csv(metadata, index_col=0)
    meta_df['sample_collection_datetime'] = \
        pd.to_datetime(meta_df['sample_collection_datetime'])
    if config is not None:
        with open(config, "r") as f:
            try:
                config = yaml.safe_load(f)
            except yaml.YAMLError as exc:
                raise ValueError('Error in config file: ' + str(exc))

    with open(os.path.join(locDir, 'data/lineages.yml'), 'r') as f:
        try:
            lineages_yml = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError('lineages.yml error, run update: ' + str(exc))

    # converts lineages_yml to a dictionary where the lineage names are the
    # keys.
    lineage_info = {}
    for lineage in lineages_yml:
        lineage_info[lineage['name']] = {'name': lineage['name'],
                                         'children': lineage['children']}
    if config is not None:
        config = checkConfig(config)
    else:
        config = {}
    df_ab_lin, df_ab_sum, dates_to_keep = get_abundance(agg_df, meta_df,
                                                        thresh,
                                                        scale_by_viral_load,
                                                        config, lineage_info)
    calc_rel_growth_rates(df_ab_lin.copy(deep=True), nboots,
                          serial_interval, output, daysIncluded=days,
                          grThresh=grthresh)


@cli.command()
@click.argument('query_mutations', type=click.Path(exists=True))
@click.argument('bam_input_dir', type=click.Path(exists=True))
@click.option('--output', default='filtered_reads.bam',
              help='Output bam file')
def filter(query_mutations, bam_input_dir, output):
    # Load data
    with open(query_mutations) as infile:
        mutations = [line[:-1] for line in infile.readlines()]

        snps = [s for s in mutations[0].split(',')]
        insertions = [s for s in mutations[1].split(',')]
        deletions = [s for s in mutations[2].split(',')]

    
    # parse tuples from indels
    if len(insertions[0]) > 0:
        insertions = [(int(s.split(':')[0][1:]), s.split(':')[1][1:-2].strip('\''))
                      for s in insertions]
    if len(deletions[0]) > 0:
        deletions = [(int(s.split(':')[0][1:]), int(s.split(':')[1][:-1]))
                     for s in deletions]

    # get loci for all mutations
    snp_sites = [int(m[1:len(m)-1])-1 for m in snps if m] # account for 1-based indexing
    indel_sites = [s[0] for s in insertions if s] +\
                  [s[0] for s in deletions if s]

    for bam in os.listdir(bam_input_dir):
        if not bam.endswith('.bam'):
            continue

        samfile = pysam.AlignmentFile(os.path.join(bam_input_dir, bam), 'rb')

        reads_considered = []

        # TODO: Only fetch reads that contain mutations of interest
        # e.g. loop over samfile.fetch("NC_045512.2", mySite,mySite+1)
        # for each mySite in sites
        if len(snp_sites) == 0:
            snp_sites = [0]
        if len(indel_sites) == 0:
            indel_sites = [0]
        min_site = min(min(snp_sites), min(indel_sites))
        max_site = max(max(snp_sites), max(indel_sites))
        itr = samfile.fetch("NC_045512.2", min_site, max_site+1)
        for x in itr:
            ref_pos = set(x.get_reference_positions())
            start = x.reference_start
            sites_in = list(ref_pos & set(snp_sites))
            
            seq = x.query_alignment_sequence
            cigar = re.findall(r'(\d+)([A-Z]{1})', x.cigarstring)

            # note: inserts add to read length, but not alignment coordinate
            if 'I' in x.cigarstring:
                i = 0
                del_spacing = 0
                ins_spacing = 0
                ranges = []  # keep track of read without insertions to find snp matches
                insert_found = False
                for m in cigar:
                    if m[1] == 'I':
                        if (start+i+del_spacing-ins_spacing, seq[i:i+int(m[0])]) in insertions:
                            reads_considered.append(x.query_name)
                            print('insertion found')
                            insert_found = True
                            break
                        i += int(m[0])
                        ins_spacing += int(m[0])
                    elif m[1] == 'D':
                        del_spacing += int(m[0])
                        continue
                    elif m[1] == 'M':
                        ranges.append([i,i+int(m[0])])
                        i += int(m[0])
                seq = ''.join([seq[r[0]:r[1]] for r in ranges])
                if insert_found:
                    continue
            if 'D' in x.cigarstring:
                #c_dict = dict(zip(x.get_reference_positions), seq)
                i = x.reference_start
                deletions_found = []

                for m in cigar:
                    if m[1] == 'M':
                        i += int(m[0])
                    elif m[1] == 'D':
                        if (i, int(m[0])) in deletions:
                            print('del found')
                            reads_considered.append((i, int(m[0])))
                        i += int(m[0])
                #for s in sites_in:
                    #if len(deletions_found)>0: 
                        #if sum([1 for d in deletions_found if d in deletions]) > 0:
                            #reads_considered.append(x.query_name)
                            #print('del found')
                            #continue
            else:
                snp_dict = {int(mut[1:len(mut)-1])-1 : mut[-1] for mut in snps}
                read_dict = dict(zip(x.get_reference_positions(), seq))

                for s in sites_in:
                    if snp_dict[s] == read_dict[s]:
                        print('snp found')
                        reads_considered.append(x.query_name)
                        break
        print(len(reads_considered))
        samfile.close()

        # Run again, this time also getting the paired read
        samfile = pysam.AlignmentFile(os.path.join(bam_input_dir, bam), 'rb')
        outfile = pysam.AlignmentFile(output, 'wb', template=samfile)

        itr = samfile.fetch('NC_045512.2', min_site, max_site+1)
        for x in itr:
            if x.query_name not in reads_considered:
                continue
            outfile.write(x)
        samfile.close()
        outfile.close()
if __name__ == '__main__':
    cli()
