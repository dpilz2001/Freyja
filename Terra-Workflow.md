To run Freyja using a web interface, we recommend using the Terra ecosystem. If you haven't used Terra before, it's pretty easy to set things up.  You'll need to:
1. Sign up for a Terra account at https://terra.bio/
2. Set up billing: All accounts get $300 in free credit (see [here](https://support.terra.bio/hc/en-us/articles/360046295092), which will allow you to run the pipeline on a ton of samples
3. Create a workspace
4. Select desired method from [dockstore](https://dockstore.org/search?entryType=workflows&search=freyja). Clicking on the Terra icon on the right hand side will take you to Terra, where you can import the method into your workspace. 
 - [Freyja_FASTQ](https://dockstore.org/workflows/github.com/theiagen/public_health_viral_genomics/Freyja_FASTQ:main?tab=info): takes you from raw data to de-mixed output in a single step (combines the ```freyja variants``` and ```freyja demix``` steps described in the README). 
 - [Freyja_Plot](https://dockstore.org/workflows/github.com/theiagen/public_health_viral_genomics/Freyja_Plot:main?tab=info): takes in output files from Freyja_FASTQ and renders plots of virus lineage fractions (using the ```freyja plot``` function described in the README)

Once you've loaded the methods into your workspace, you can go ahead and run the workflow on your data. You'll first want to run the Freyja_FASTQ method on some raw data. If you don't have any available, you can test the method out using [this one](test.fastq.gz).  
4. Load in data and run the pipeline!

(still under construction)