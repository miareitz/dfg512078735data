set terminal pdfcairo enhanced color font ',10'
set datafile separator comma
set style data histograms
set style histogram rowstacked
set style fill solid border -1
set boxwidth 0.75
set grid ytics
set xlabel 'MPI ranks'
set ylabel 'Mean duration [s]'
set output 'plots/rf26b_phase_breakdown_s0.000002_m1.pdf'
set title 'RF26b LCS phase breakdown (s=0.000002, actual/requested SDC=1)'
plot 'rf26b_scaling.csv' using (strcol(3) eq '0.000002' && strcol(4) eq '1' && strcol(1) eq 'ok' ? $13 : 1/0):xtic(6) title 'first', \
     '' using (strcol(3) eq '0.000002' && strcol(4) eq '1' && strcol(1) eq 'ok' ? $14 : 1/0) title 'twin', \
     '' using (strcol(3) eq '0.000002' && strcol(4) eq '1' && strcol(1) eq 'ok' ? $15 : 1/0) title 'traversal', \
     '' using (strcol(3) eq '0.000002' && strcol(4) eq '1' && strcol(1) eq 'ok' ? $16 : 1/0) title 'third'
