options(stringsAsFactors=FALSE, warn=1)
a <- commandArgs(TRUE)
stopifnot(length(a)==2L)
root <- normalizePath(a[1],mustWork=TRUE)
out <- normalizePath(a[2],mustWork=TRUE)
for (p in c('vegan','permute')) if (!requireNamespace(p,quietly=TRUE)) stop('Missing package: ',p)
write_tsv <- function(x,file) write.table(x,file,sep='\t',row.names=FALSE,quote=FALSE,na='NA')
slug <- function(x) gsub('[^A-Za-z0-9_.-]','_',x)
capture_notes <- function(expr) {
 notes <- character()
 value <- tryCatch(withCallingHandlers(expr,warning=function(w) {notes <<- c(notes,conditionMessage(w));invokeRestart('muffleWarning')}),error=function(e)e)
 list(value=value,notes=paste(unique(notes),collapse=' | '))
}
nperm_requested <- 9999L
cache <- new.env(parent=emptyenv())
perm_dir <- file.path(out,'permutations');dir.create(perm_dir)
stable_permutations <- function(md,ctl) {
 key <- paste(farm,domain,unique(md$cohort),paste(md$sample_name,md$collection_date,md$treatment_label,sep=':',collapse='|'),sep='|')
 if (exists(key,envir=cache,inherits=FALSE)) return(get(key,envir=cache)$matrix)
 seed <- 20260924
 for (v in utf8ToInt(key)) seed <- (seed*131+v) %% 2147483646
 set.seed(as.integer(seed)+1L)
 mat <- permute::shuffleSet(nrow(md),nset=nperm_requested,control=ctl)
 stopifnot(ncol(mat)==nrow(md),all(apply(mat,1,function(x) identical(sort(as.integer(x)),seq_len(nrow(md))))))
 if (length(unique(md$collection_date))>1L) stopifnot(all(apply(mat,1,function(x) all(md$collection_date[x]==md$collection_date))))
 item <- list(key=key,seed=seed+1L,samples=md$sample_name,dates=md$collection_date,matrix=mat)
 assign(key,item,envir=cache)
 saveRDS(item,file.path(perm_dir,sprintf('permutation_%04d.rds',length(ls(cache)))))
 mat
}
source(file.path(root,'cohort_test.R'))
# Verify reuse and within-date restrictions before any real cohort tests.
farm <- 'SELFTEST';domain <- 'SELFTEST'
m <- data.frame(sample_name=paste0('s',1:6),collection_date=rep(c('a','b'),each=3),treatment_label=rep(c('x','y','z'),2),cohort='selftest')
ctl <- permute::how(nperm=99,blocks=factor(m$collection_date))
p1 <- stable_permutations(m,ctl); runif(20);p2 <- stable_permutations(m,ctl)
stopifnot(identical(p1,p2))
writeLines('PASS: cached matrices identical; within-date restriction and permutation rows verified',file.path(out,'PERMUTATION_SELFTEST_PASS.txt'))
all_results <- list()
for(domain in c('archaea','bacteria')) {
 counts <- as.matrix(read.delim(file.path(root,'inputs',paste0(domain,'_counts.tsv')),row.names=1,check.names=FALSE))
 storage.mode(counts)<-'numeric'
 stopifnot(!anyDuplicated(rownames(counts)),all(is.finite(counts)),all(counts>=0),all(counts==floor(counts)),!any(rownames(counts)%in%c('C108','C111','D108','D111')))
 for(farm in c('OatmanFlatsRanch','PageSpringsCellars','Whipstone')) {
  md <- read.delim(file.path(root,'inputs',paste0(farm,'_',domain,'_metadata.tsv')),check.names=FALSE)
  md <- md[order(md$sample_name),,drop=FALSE]
  md$descriptive_reference <- tolower(as.character(md$descriptive_reference))=='true'
  stopifnot(!anyNA(md$sample_name),!anyDuplicated(md$sample_name),all(md$sample_name%in%rownames(counts)))
  cts <- counts[md$sample_name,,drop=FALSE]
  stopifnot(all(rowSums(cts)==md$target))
  results <- list()
  for(threshold in c(0,100,500,1000,2000,5000)) {
   variant <- paste0('depth_',threshold)
   folder <- file.path(out,farm,domain,variant);dir.create(folder,recursive=TRUE)
   for(cohort in sort(unique(md$cohort))) {
    full <- md[md$cohort==cohort,,drop=FALSE]
    kept <- full[full$target>0 & full$target>=threshold,,drop=FALSE]
    write_tsv(data.frame(sample=full$sample_name,target_reads=full$target,included=full$sample_name%in%kept$sample_name),file.path(folder,paste0(slug(cohort),'_selection.tsv')))
    if(!nrow(kept)) {
     # Keep the empty selection explicit without invoking the single-cohort function.
     writeLines('No samples retained; no test performed',file.path(folder,paste0(slug(cohort),'_NO_SAMPLES.txt')))
     next
    }
    r <- cohort_test(cts[kept$sample_name,,drop=FALSE],kept,variant,folder,sort(unique(full$treatment_label)))
    r$farm<-farm;r$domain<-domain;r$minimum_reads<-threshold
    r$sample_set<-paste(kept$sample_name,collapse=';')
    results[[length(results)+1L]]<-r
   }
  }
  status<-do.call(rbind,results)
  for(v in unique(status$analysis_variant)) for(pair in list(c('treatment_p','treatment_q_BH'),c('dispersion_p','dispersion_q_BH'))) {
   i<-which(status$analysis_variant==v & is.finite(status[[pair[1]]]))
   status[i,pair[2]]<-p.adjust(status[i,pair[1]],method='BH')
  }
  # Identical input cohorts must have identical raw statistics across variants.
  for(g in split(seq_len(nrow(status)),paste(status$cohort,status$sample_set,sep='|'))) {
   for(col in c('treatment_R2','treatment_F','treatment_p','dispersion_F','dispersion_p')) {
    vals<-status[g,col];stopifnot(length(unique(vals[is.finite(vals)]))<=1L)
   }
  }
  write_tsv(status,file.path(out,farm,domain,'analyses_status.tsv'))
  all_results[[length(all_results)+1L]]<-status
 }
}
write_tsv(do.call(rbind,all_results),file.path(out,'ALL_ANALYSES_STATUS.tsv'))
writeLines(capture.output(sessionInfo()),file.path(out,'sessionInfo.txt'))
writeLines('PASS: identical cohort inputs give identical raw statistics across depth variants',file.path(out,'REUSE_VALIDATION_PASS.txt'))
writeLines('Completed downstream depth sensitivity review; no primary threshold selected',file.path(out,'COMPLETE.txt'))
